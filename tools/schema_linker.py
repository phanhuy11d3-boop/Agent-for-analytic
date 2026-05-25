import re
import unicodedata
from typing import Any

from tools.semantic_inference import best_role, load_taxonomy


_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "what", "which", "where", "when",
    "how", "why", "are", "is", "of", "to", "in", "by", "a", "an", "la", "nhung",
    "cac", "cho", "voi", "hay", "khong", "nao", "dau", "co", "kha", "nang",
    "no", "dua", "tren", "duoc", "can", "tim", "ve", "cua", "va", "mot",
}

_ROLE_WEIGHTS = {
    "outcome": 0.56,
    "measure": 0.48,
    "dimension": 0.38,
    "datetime": 0.32,
    "binary_flag": 0.28,
    "identifier": 0.08,
    "text": 0.06,
}


def _normalise(text: Any) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _terms(text: Any) -> set[str]:
    return {term for term in _normalise(text).split() if len(term) > 1 and term not in _STOPWORDS}


def _overlap_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    exact = len(left & right)
    partial = 0
    for a in left:
        if len(a) < 3:
            continue
        for b in right:
            if len(b) >= 3 and (a in b or b in a):
                partial += 1
                break
    return min(1.0, (exact * 0.55 + partial * 0.25) / max(1, len(left)))


def _sample_value_score(col: dict[str, Any], question_terms: set[str]) -> float:
    values = " ".join(str(item.get("label", "")) for item in col.get("sample_top_values", [])[:8])
    return _overlap_score(question_terms, _terms(values)) * 0.5


def _context_score(col_name: str, question_terms: set[str], semantic_context: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    descriptions = semantic_context.get("column_descriptions") or {}
    description = descriptions.get(col_name, "")
    desc_score = _overlap_score(question_terms, _terms(description))
    if desc_score:
        score += desc_score * 0.45
        reasons.append("matches saved column description")

    if col_name == semantic_context.get("outcome_column"):
        score += 0.5
        reasons.append("confirmed outcome column")
    if col_name == semantic_context.get("primary_metric"):
        score += 0.42
        reasons.append("confirmed primary metric")
    return score, reasons


def _phrase_in_text(phrase: str, text: str) -> bool:
    phrase = _normalise(phrase)
    text = _normalise(text)
    return bool(phrase and phrase in text)


def _concept_matches(col_name: str, question: str) -> list[dict[str, Any]]:
    taxonomy = load_taxonomy()
    normalized_col = _normalise(col_name)
    matches: list[dict[str, Any]] = []
    if not normalized_col:
        return matches

    for concept_name, concept in taxonomy.get("concepts", {}).items():
        question_hits = [
            phrase for phrase in concept.get("question_terms", [])
            if _phrase_in_text(str(phrase), question)
        ]
        if not question_hits:
            continue

        strong_hits = [
            term for term in concept.get("strong_column_terms", [])
            if _phrase_in_text(str(term), normalized_col)
        ]
        supporting_hits = [
            term for term in concept.get("supporting_column_terms", [])
            if _phrase_in_text(str(term), normalized_col)
        ]
        low_priority_hits = [
            term for term in concept.get("low_priority_column_terms", [])
            if _phrase_in_text(str(term), normalized_col)
        ]
        if strong_hits:
            score = min(1.0, 0.68 + 0.08 * len(strong_hits) + 0.03 * len(question_hits))
        elif supporting_hits:
            score = min(0.58, 0.34 + 0.06 * len(supporting_hits) + 0.03 * len(question_hits))
        else:
            continue
        if low_priority_hits:
            score = min(score, 0.25)
        matches.append({
            "concept": concept_name,
            "score": round(score, 3),
            "question_terms": question_hits[:4],
            "column_terms": (strong_hits or supporting_hits)[:4],
        })

    matches.sort(key=lambda item: item["score"], reverse=True)
    return matches


def link_schema(
    question: str,
    table_profile: dict[str, Any],
    semantic_context: dict[str, Any] | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    context = semantic_context or {}
    question_terms = _terms(question)
    linked: list[dict[str, Any]] = []

    for col in table_profile.get("columns", []):
        name = str(col.get("name", ""))
        role = best_role(col)
        name_score = _overlap_score(question_terms, _terms(name))
        value_score = _sample_value_score(col, question_terms)
        context_boost, context_reasons = _context_score(name, question_terms, context)
        concept_matches = _concept_matches(name, question)
        concept_score = concept_matches[0]["score"] if concept_matches else 0.0
        role_score = _ROLE_WEIGHTS.get(role, 0.05)
        role_confidence = float((col.get("role_candidates") or [{}])[0].get("confidence", 0) or 0)
        completeness = max(0.0, 1.0 - float(col.get("missing_pct", 0) or 0) / 100)
        distinct = int(col.get("sample_distinct_count", 0) or 0)
        cardinality = 0.18 if 1 < distinct <= 50 else 0.08 if distinct > 50 else 0.0
        penalty = 0.0
        if role in {"identifier", "text"} and not max(name_score, value_score, context_boost):
            penalty += 0.35
        if float(col.get("missing_pct", 0) or 0) >= 80:
            penalty += 0.18
        if distinct <= 1:
            penalty += 0.12

        score = max(0.0, min(1.0, (
            role_score
            + role_confidence * 0.18
            + name_score * 0.32
            + value_score * 0.22
            + context_boost
            + concept_score * 0.55
            + completeness * 0.14
            + cardinality
            - penalty
        )))

        reasons = []
        if name_score:
            reasons.append("name matches the question")
        if value_score:
            reasons.append("sample values match the question")
        if concept_matches:
            labels = ", ".join(match["concept"].replace("_", " ") for match in concept_matches[:2])
            reasons.append(f"matches semantic concept: {labels}")
        reasons.extend(context_reasons)
        if role in {"measure", "dimension", "datetime", "outcome"}:
            reasons.append(f"usable {role.replace('_', ' ')} role")
        if col.get("missing_pct", 0) >= 40:
            reasons.append("high missingness; use with caution")
        if not reasons:
            reasons.append("generic low-confidence candidate")

        if score >= 0.18:
            linked.append({
                "column": name,
                "role": role,
                "score": round(score, 3),
                "confidence": round(role_confidence, 3),
                "missing_pct": col.get("missing_pct", 0),
                "sample_distinct_count": distinct,
                "sample_top_values": col.get("sample_top_values", [])[:8],
                "name_score": round(name_score, 3),
                "value_score": round(value_score, 3),
                "context_score": round(context_boost, 3),
                "concept_score": round(concept_score, 3),
                "concepts": concept_matches[:3],
                "reason": "; ".join(dict.fromkeys(reasons)),
            })

    linked.sort(key=lambda item: item["score"], reverse=True)
    return linked[:limit]
