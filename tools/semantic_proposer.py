import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from config import invoke_groq
from tools.semantic_inference import best_role, load_taxonomy


SYSTEM_PROMPT = """You propose semantic context for arbitrary uploaded tables.
Use only the provided profile: table name, column metadata, top values, and sample rows.

Return only valid JSON:
{
  "context": {
    "table_purpose": "",
    "row_grain": "",
    "primary_metric": "",
    "outcome_column": "",
    "positive_outcome_value": "",
    "negative_outcome_value": "",
    "column_descriptions": {}
  },
  "confidence": {
    "table_purpose": 0.0,
    "row_grain": 0.0,
    "primary_metric": 0.0,
    "outcome_column": 0.0,
    "positive_outcome_value": 0.0
  },
  "rationale": ["short reason"],
  "warnings": ["short warning"],
  "requires_confirmation": ["field_name"]
}

Rules:
- Do not invent a business outcome if no column strongly supports it.
- Mark low-confidence or domain-dependent fields in requires_confirmation.
- If the table appears to have no target/outcome, leave outcome fields blank and say so.
- Never assume what 0 or 1 means unless top values and column name make it clear.
"""

def _normalise(value: Any) -> str:
    text = str(value or "").lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _proposal_hint_list(name: str) -> list[str]:
    hints = load_taxonomy().get("proposal_hints", {})
    values = hints.get(name, [])
    if not isinstance(values, list):
        return []
    return [_normalise(value) for value in values if _normalise(value)]


def _proposal_hint_set(name: str) -> set[str]:
    return set(_proposal_hint_list(name))


def _binary_hint_set(kind: str) -> set[str]:
    values = load_taxonomy().get("binary_values", {}).get(kind, [])
    if not isinstance(values, list):
        return set()
    return {_normalise(value) for value in values if _normalise(value)}


def _column_names(table_profile: dict[str, Any]) -> set[str]:
    return {str(col.get("name", "")) for col in table_profile.get("columns", [])}


def _top_labels(col: dict[str, Any], limit: int = 6) -> list[str]:
    labels = []
    for item in col.get("sample_top_values", [])[:limit]:
        label = item.get("label")
        if label is not None and str(label).strip() != "":
            labels.append(str(label))
    return labels


def _field_confidence(existing_value: Any, proposed_value: Any, proposed_confidence: float) -> float:
    if existing_value:
        return 1.0
    if proposed_value:
        return round(max(0.0, min(1.0, proposed_confidence)), 2)
    return 0.0


def _empty_context(table_name: str | None) -> dict[str, Any]:
    return {
        "table_name": table_name,
        "table_purpose": "",
        "row_grain": "",
        "primary_metric": "",
        "outcome_column": "",
        "positive_outcome_value": "",
        "negative_outcome_value": "",
        "column_descriptions": {},
    }


def _table_label(table_name: str | None) -> str:
    clean = str(table_name or "selected table")
    clean = re.sub(r"^ds_", "", clean)
    clean = clean.replace("_", " ").strip()
    return clean or "selected table"


def _is_identifier(col: dict[str, Any]) -> bool:
    role = best_role(col)
    name = _normalise(col.get("name"))
    distinct = int(col.get("sample_distinct_count", 0) or 0)
    rows = int(col.get("row_count", 0) or 0)
    return role == "identifier" or name.endswith("_id") or (rows > 0 and distinct == rows)


def _outcome_score(col: dict[str, Any]) -> float:
    name = _normalise(col.get("name"))
    name_parts = set(name.split("_"))
    role = best_role(col)
    distinct = int(col.get("sample_distinct_count", 0) or 0)
    missing = float(col.get("missing_pct", 0) or 0)
    labels = {_normalise(v) for v in _top_labels(col)}
    strong_outcome_hints = _proposal_hint_set("strong_outcome_name_terms")
    weak_outcome_hints = _proposal_hint_set("weak_outcome_name_terms")
    positive_hints = _proposal_hint_set("positive_values") | _binary_hint_set("positive")
    negative_hints = _proposal_hint_set("negative_values") | _binary_hint_set("negative")
    strong_name_match = any(term in name_parts or term in name for term in strong_outcome_hints)
    weak_name_match = any(term in name_parts or term in name for term in weak_outcome_hints)

    score = 0.0
    if role == "outcome":
        score += 0.45
    if role == "binary_flag":
        score += 0.08
    if labels and len(labels) <= 3:
        score += 0.08
    if distinct and distinct <= 3:
        score += 0.15
    if strong_name_match:
        score += 0.35
    elif weak_name_match:
        score += 0.12
    if labels & (positive_hints | negative_hints):
        score += 0.04
    if missing >= 60:
        score -= 0.12
    if _is_identifier(col):
        score -= 0.5
    if not strong_name_match and role != "outcome":
        score = min(score, 0.55)
    return round(max(0.0, min(1.0, score)), 3)


def _metric_score(col: dict[str, Any]) -> float:
    role = best_role(col)
    name = _normalise(col.get("name"))
    name_parts = set(name.split("_"))
    if role != "measure":
        return 0.0
    if any(term in name_parts for term in _proposal_hint_set("low_value_metric_terms")):
        return 0.0
    missing = float(col.get("missing_pct", 0) or 0)
    distinct = int(col.get("sample_distinct_count", 0) or 0)
    score = 0.45 + (1 - missing / 100) * 0.25
    score += 0.15 if distinct > 10 else 0.05
    if any(term in name for term in _proposal_hint_set("metric_name_terms")):
        score += 0.18
    if _is_identifier(col):
        score -= 0.45
    return round(max(0.0, min(1.0, score)), 3)


def _positive_value(col: dict[str, Any]) -> tuple[str, str, float]:
    labels = _top_labels(col)
    normalised = {_normalise(v): v for v in labels}
    positive_hints = _proposal_hint_list("positive_values")
    positive_hints = positive_hints or list(_binary_hint_set("positive"))
    binary_positive = _binary_hint_set("positive")
    for value in positive_hints:
        if value in normalised:
            return str(normalised[value]), "", 0.7 if value in binary_positive else 0.62
    negative_hints = _proposal_hint_list("negative_values")
    negative_hints = negative_hints or list(_binary_hint_set("negative"))
    for value in negative_hints:
        if value in normalised and len(labels) == 2:
            other = next((label for label in labels if _normalise(label) != value), "")
            if other:
                return str(other), str(normalised[value]), 0.48
    return "", "", 0.0


def _row_grain(table_profile: dict[str, Any]) -> tuple[str, float]:
    table = _table_label(table_profile.get("table_name"))
    identifiers = [col for col in table_profile.get("columns", []) if _is_identifier(col)]
    if identifiers:
        name = identifiers[0].get("name")
        return f"One row represents one {table} record, identified by `{name}`.", 0.62
    return f"One row appears to represent one {table} record.", 0.38


def _table_purpose(table_profile: dict[str, Any]) -> tuple[str, float]:
    table = _table_label(table_profile.get("table_name"))
    roles = table_profile.get("role_counts", {})
    if roles.get("outcome") or roles.get("binary_flag"):
        return f"Records used to track or analyze {table}, including candidate status/outcome fields.", 0.52
    if roles.get("measure") and roles.get("dimension"):
        return f"Records used to summarize and compare {table} across available dimensions and measures.", 0.48
    return f"Uploaded dataset containing {table} records for exploratory analysis.", 0.34


def _deterministic_proposal(
    table_profile: dict[str, Any],
    existing_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing = existing_context or {}
    context = _empty_context(table_profile.get("table_name") or existing.get("table_name"))
    for key in context:
        if key in existing and existing.get(key):
            context[key] = existing[key]

    purpose, purpose_conf = _table_purpose(table_profile)
    grain, grain_conf = _row_grain(table_profile)
    if not context.get("table_purpose"):
        context["table_purpose"] = purpose
    if not context.get("row_grain"):
        context["row_grain"] = grain

    columns = table_profile.get("columns", [])
    outcome_candidates = sorted(
        [(col, _outcome_score(col)) for col in columns],
        key=lambda item: item[1],
        reverse=True,
    )
    metric_candidates = sorted(
        [(col, _metric_score(col)) for col in columns],
        key=lambda item: item[1],
        reverse=True,
    )

    outcome_conf = 0.0
    positive_conf = 0.0
    if not context.get("outcome_column") and outcome_candidates:
        col, score = outcome_candidates[0]
        if score >= 0.62:
            context["outcome_column"] = col.get("name", "")
            pos, neg, positive_conf = _positive_value(col)
            context["positive_outcome_value"] = pos
            context["negative_outcome_value"] = neg
            outcome_conf = score
    elif context.get("outcome_column"):
        outcome_conf = 1.0
        match = next((col for col in columns if col.get("name") == context["outcome_column"]), None)
        if match and not context.get("positive_outcome_value"):
            pos, neg, positive_conf = _positive_value(match)
            context["positive_outcome_value"] = pos
            context["negative_outcome_value"] = neg
        elif context.get("positive_outcome_value"):
            positive_conf = 1.0

    metric_conf = 0.0
    if not context.get("primary_metric") and metric_candidates:
        col, score = metric_candidates[0]
        if score >= 0.45:
            context["primary_metric"] = col.get("name", "")
            metric_conf = score
    elif context.get("primary_metric"):
        metric_conf = 1.0

    confidence = {
        "table_purpose": _field_confidence(existing.get("table_purpose"), context.get("table_purpose"), purpose_conf),
        "row_grain": _field_confidence(existing.get("row_grain"), context.get("row_grain"), grain_conf),
        "primary_metric": _field_confidence(existing.get("primary_metric"), context.get("primary_metric"), metric_conf),
        "outcome_column": _field_confidence(existing.get("outcome_column"), context.get("outcome_column"), outcome_conf),
        "positive_outcome_value": _field_confidence(
            existing.get("positive_outcome_value"),
            context.get("positive_outcome_value"),
            positive_conf,
        ),
    }
    requires_confirmation = [
        field for field, score in confidence.items()
        if field in {"table_purpose", "row_grain", "outcome_column", "positive_outcome_value"} and score < 0.75
    ]
    warnings = []
    if not context.get("outcome_column"):
        warnings.append("No high-confidence outcome column was detected; outcome-based questions should be blocked or treated as proxy-only.")
    elif not context.get("positive_outcome_value"):
        warnings.append("A candidate outcome column was detected, but the positive/desired value still needs confirmation.")

    rationale = [
        "Proposal is based on column names, inferred roles, missingness, cardinality, and top sample values.",
    ]
    if context.get("primary_metric"):
        rationale.append(f"Primary metric candidate: `{context['primary_metric']}`.")
    if context.get("outcome_column"):
        rationale.append(f"Outcome candidate: `{context['outcome_column']}`.")

    return {
        "version": "semantic-proposal-v1",
        "source": "deterministic_profile",
        "context": context,
        "confidence": confidence,
        "rationale": rationale,
        "warnings": warnings,
        "requires_confirmation": requires_confirmation,
        "candidates": {
            "outcome_columns": [
                {"column": col.get("name"), "score": score, "top_values": _top_labels(col)}
                for col, score in outcome_candidates[:5]
                if score > 0
            ],
            "primary_metrics": [
                {"column": col.get("name"), "score": score}
                for col, score in metric_candidates[:5]
                if score > 0
            ],
        },
    }


def _proposal_payload(table_profile: dict[str, Any], question: str) -> dict[str, Any]:
    columns = []
    for col in table_profile.get("columns", [])[:80]:
        columns.append({
            "name": col.get("name"),
            "data_type": col.get("data_type"),
            "role": best_role(col),
            "role_candidates": col.get("role_candidates", [])[:3],
            "missing_pct": col.get("missing_pct"),
            "sample_distinct_count": col.get("sample_distinct_count"),
            "sample_top_values": col.get("sample_top_values", [])[:6],
            "min": col.get("min"),
            "max": col.get("max"),
        })
    return {
        "question": question,
        "table_name": table_profile.get("table_name"),
        "row_count": table_profile.get("row_count"),
        "columns_total": table_profile.get("columns_total"),
        "columns": columns,
        "sample_rows": table_profile.get("sample_rows", [])[:5],
    }


def _parse_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {}


def _merge_llm(base: dict[str, Any], llm_value: dict[str, Any], table_profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(llm_value, dict):
        return base
    columns = _column_names(table_profile)
    merged = json.loads(json.dumps(base, ensure_ascii=False))
    context = merged["context"]
    llm_context = llm_value.get("context") if isinstance(llm_value.get("context"), dict) else {}
    merge_warnings: list[str] = []

    for field in ("table_purpose", "row_grain"):
        value = str(llm_context.get(field) or "").strip()
        if value:
            context[field] = value
            merged["confidence"][field] = max(float(merged["confidence"].get(field, 0) or 0), 0.6)

    value = str(llm_context.get("primary_metric") or "").strip()
    if value and value in columns:
        context["primary_metric"] = value
        merged["confidence"]["primary_metric"] = max(float(merged["confidence"].get("primary_metric", 0) or 0), 0.65)

    outcome_value = str(llm_context.get("outcome_column") or "").strip()
    if outcome_value and outcome_value in columns:
        match = next((col for col in table_profile.get("columns", []) if col.get("name") == outcome_value), None)
        if match and _outcome_score(match) >= 0.62:
            context["outcome_column"] = outcome_value
            merged["confidence"]["outcome_column"] = max(float(merged["confidence"].get("outcome_column", 0) or 0), 0.65)
        else:
            merge_warnings.append(f"LLM suggested `{outcome_value}` as an outcome, but profile evidence was too weak; user confirmation is required.")

    for field in ("positive_outcome_value", "negative_outcome_value"):
        value = str(llm_context.get(field) or "").strip()
        if value and context.get("outcome_column"):
            context[field] = value
            merged["confidence"][field] = max(float(merged["confidence"].get(field, 0) or 0), 0.55)

    descriptions = llm_context.get("column_descriptions")
    if isinstance(descriptions, dict):
        context["column_descriptions"] = {
            str(k): str(v)
            for k, v in descriptions.items()
            if k in columns and str(v).strip()
        }

    for key in ("rationale", "warnings", "requires_confirmation"):
        value = llm_value.get(key)
        if isinstance(value, list):
            merged[key] = [str(item) for item in value[:8]]
    if merge_warnings:
        merged["warnings"] = list(dict.fromkeys([*(merged.get("warnings") or []), *merge_warnings]))
    merged["source"] = "llm_profile_with_deterministic_fallback"
    return merged


def propose_semantic_context(
    table_profile: dict[str, Any],
    question: str = "",
    existing_context: dict[str, Any] | None = None,
    use_llm: bool = False,
) -> dict[str, Any]:
    base = _deterministic_proposal(table_profile, existing_context)
    if not use_llm:
        return base

    try:
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=json.dumps(_proposal_payload(table_profile, question), ensure_ascii=False, default=str)),
        ]
        llm_value = _parse_json(invoke_groq(messages, temperature=0).content)
        return _merge_llm(base, llm_value, table_profile)
    except Exception as exc:
        base.setdefault("warnings", []).append(f"LLM semantic proposal was unavailable; using deterministic fallback. ({exc})")
        return base
