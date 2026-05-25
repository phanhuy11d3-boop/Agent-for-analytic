import re
import unicodedata
from typing import Any


AGG_AVG_TERMS = {
    "avg",
    "average",
    "mean",
    "median",
    "trung binh",
}
AGG_COUNT_TERMS = {
    "count",
    "number of",
    "frequency",
    "record",
    "records",
    "row",
    "rows",
    "so luong",
    "bao nhieu",
}
EXPLICIT_AGG_TERMS = {
    "top",
    "bottom",
    "highest",
    "lowest",
    "most",
    "least",
    "rank",
    "compare",
    "breakdown",
    "by",
    "versus",
    "vs",
    "cao nhat",
    "thap nhat",
    "xep hang",
    "so sanh",
    "chia theo",
    "theo",
}
_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "what", "which", "where", "when",
    "how", "why", "are", "is", "of", "to", "in", "by", "a", "an", "create",
    "creates", "created", "most", "least", "top", "highest", "lowest", "rank",
    "la", "nhung", "cac", "cho", "voi", "hay", "khong", "nao", "dau", "co",
    "duoc", "dua", "tren", "va", "theo", "chia",
}
_MULTI_DIMENSION_TERMS = {
    "split",
    "breakdown",
    "break down",
    "grouped by",
    "by",
    "across",
    "per",
    "versus",
    "vs",
    "compare",
    "comparison",
    "chia theo",
    "theo",
    "so sanh",
}


def _normalise(text: Any) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _singular(term: str) -> str:
    if term.endswith("ies") and len(term) > 4:
        return term[:-3] + "y"
    if term.endswith("s") and len(term) > 3:
        return term[:-1]
    return term


def _terms(text: Any) -> set[str]:
    return {
        _singular(term)
        for term in _normalise(text).split()
        if len(term) > 1 and term not in _STOPWORDS
    }


def _contains_any(text: str, terms: set[str]) -> bool:
    normalized = _normalise(text)
    return any(_normalise(term) and _normalise(term) in normalized for term in terms)


def _match_score(item: dict[str, Any]) -> float:
    return max(
        float(item.get("name_score", 0) or 0),
        float(item.get("value_score", 0) or 0),
        float(item.get("concept_score", 0) or 0),
        float(item.get("context_score", 0) or 0),
    )


def _concept_names(item: dict[str, Any]) -> set[str]:
    return {str(match.get("concept") or "") for match in item.get("concepts", [])}


def _concept_question_terms(item: dict[str, Any]) -> set[str]:
    terms: set[str] = set()
    for match in item.get("concepts", []):
        for term in match.get("question_terms", []) or []:
            terms.update(_terms(term))
    return terms


def _column_profile(table_profile: dict[str, Any], column: str) -> dict[str, Any]:
    return next((col for col in table_profile.get("columns", []) if col.get("name") == column), {})


def _is_numeric_profile(profile: dict[str, Any]) -> bool:
    text = str(profile.get("data_type") or "").lower()
    return any(token in text for token in ("int", "numeric", "double", "real", "decimal", "float"))


def _identifier_name(column: str) -> bool:
    normalized = _normalise(column)
    tokens = normalized.split()
    return normalized in {"id", "key", "code"} or bool(tokens and tokens[-1] in {"id", "key", "code"})


def _is_query_matched(item: dict[str, Any], threshold: float = 0.12) -> bool:
    return _match_score(item) >= threshold


def _direct_dimension_level(item: dict[str, Any], question: str) -> int:
    """Prefer the actual field the user named over broad concept siblings."""
    column = str(item.get("column") or "")
    normalized_column = _normalise(column)
    normalized_question = _normalise(question)
    if normalized_column and f" {normalized_column} " in f" {normalized_question} ":
        return 3

    question_terms = _terms(question)
    column_terms = _terms(column)
    if column_terms and column_terms <= question_terms:
        return 2
    if column_terms & question_terms:
        return 1
    if float(item.get("name_score", 0) or 0) >= 0.12:
        return 1
    if max(float(item.get("value_score", 0) or 0), float(item.get("context_score", 0) or 0)) >= 0.2:
        return 1
    return 0


def _matched_sample_value_filter(item: dict[str, Any], question: str) -> dict[str, Any] | None:
    question_terms = _terms(question)
    normalized_question = f" {_normalise(question)} "
    for value in item.get("sample_top_values", []) or []:
        label = str(value.get("label", "")).strip()
        normalized_label = _normalise(label)
        if not normalized_label or not re.search(r"[a-z]", normalized_label):
            continue
        label_terms = _terms(label)
        if f" {normalized_label} " in normalized_question or (label_terms and label_terms <= question_terms):
            return {
                "column": item.get("column"),
                "value": label,
                "label": label,
                "match": "sample_value",
                "confidence": round(max(float(item.get("value_score", 0) or 0), 0.5), 2),
            }
    return None


def _select_filters(
    linked_columns: list[dict[str, Any]],
    selected_measures: list[dict[str, Any]],
    selected_dimensions: list[dict[str, Any]],
    question: str,
    limit: int = 3,
) -> list[dict[str, Any]]:
    used_columns = {
        str(item.get("column") or "")
        for item in [*selected_measures, *selected_dimensions]
        if item.get("column")
    }
    filters: list[dict[str, Any]] = []
    for item in linked_columns:
        column = str(item.get("column") or "")
        if not column or column in used_columns:
            continue
        if item.get("role") not in {"dimension", "binary_flag", "outcome", "text"}:
            continue
        match = _matched_sample_value_filter(item, question)
        if match:
            filters.append(match)
        if len(filters) >= limit:
            break
    return filters


def _requests_multi_dimension(question: str) -> bool:
    return _contains_any(question, _MULTI_DIMENSION_TERMS)


def _dimension_limit(question: str, dimensions: list[dict[str, Any]], max_limit: int = 2) -> int:
    direct_matches = [item for item in dimensions if _direct_dimension_level(item, question) > 0]
    if not direct_matches:
        concepts = {
            concept
            for item in dimensions
            for concept in _concept_names(item)
            if _concept_question_terms(item) & _terms(question)
        }
        if _requests_multi_dimension(question) and len(concepts) >= 2:
            return min(max_limit, len(concepts))
        return 1
    if len(direct_matches) == 1:
        concepts = {
            concept
            for item in dimensions
            for concept in _concept_names(item)
            if _concept_question_terms(item) & _terms(question)
        }
        if _requests_multi_dimension(question) and len(concepts) >= 2:
            return min(max_limit, len(concepts))
        return 1

    if _requests_multi_dimension(question):
        return min(max_limit, len(direct_matches))

    question_terms = _terms(question)
    direct_question_terms = set()
    for item in direct_matches:
        direct_question_terms.update(_terms(item.get("column", "")))
    if len(direct_question_terms & question_terms) >= 2:
        return min(max_limit, len(direct_matches))
    return 1


def _is_metric_item(item: dict[str, Any], table_profile: dict[str, Any]) -> bool:
    if not _is_query_matched(item):
        return False
    if item.get("role") == "measure":
        return True
    profile = _column_profile(table_profile, str(item.get("column") or ""))
    return _is_numeric_profile(profile) and not _identifier_name(str(item.get("column") or ""))


def _is_count_groupable_item(item: dict[str, Any], table_profile: dict[str, Any], question: str) -> bool:
    if not _is_query_matched(item):
        return False
    direct_level = _direct_dimension_level(item, question)
    role = item.get("role")
    if role in {"dimension", "binary_flag", "datetime"}:
        return True
    if role == "identifier" and direct_level > 0:
        return True
    profile = _column_profile(table_profile, str(item.get("column") or ""))
    distinct = int(item.get("sample_distinct_count", 0) or 0)
    return (
        role == "measure"
        and direct_level > 0
        and _is_numeric_profile(profile)
        and not _identifier_name(str(item.get("column") or ""))
        and 1 < distinct <= 120
    )


def _aggregation(question: str, measures: list[dict[str, Any]]) -> str:
    if _contains_any(question, AGG_COUNT_TERMS):
        return "count"
    if _contains_any(question, AGG_AVG_TERMS):
        return "avg"
    # Measures explicitly linked to business totals should default to SUM for ranking.
    return "sum"


def _dimension_priority(item: dict[str, Any]) -> tuple[int, float]:
    distinct = int(item.get("sample_distinct_count", 0) or 0)
    cardinality_penalty = distinct if distinct > 1 else 9999
    return (cardinality_penalty, -float(item.get("score", 0) or 0))


def _direct_dimension_priority(item: dict[str, Any], question: str) -> tuple[int, float, float]:
    return (
        -_direct_dimension_level(item, question),
        -float(item.get("name_score", 0) or 0),
        -_match_score(item),
    )


def _select_dimensions(dimensions: list[dict[str, Any]], question: str, limit: int | None = None) -> list[dict[str, Any]]:
    if not dimensions:
        return []
    budget = min(limit or _dimension_limit(question, dimensions), len(dimensions), 2)
    selected: list[dict[str, Any]] = []

    direct_matches = [item for item in dimensions if _direct_dimension_level(item, question) > 0]
    direct_matches.sort(key=lambda item: _direct_dimension_priority(item, question))
    for item in direct_matches:
        if item not in selected:
            selected.append(item)
        if len(selected) >= budget:
            return selected

    dimensions.sort(key=_dimension_priority)
    for item in dimensions:
        if item not in selected:
            selected.append(item)
        if len(selected) >= budget:
            break
    return selected


def build_explicit_aggregate_plan(
    question: str,
    intent: dict[str, Any],
    table_profile: dict[str, Any],
    linked_columns: list[dict[str, Any]],
) -> dict[str, Any]:
    if not table_profile.get("table_name"):
        return {"is_explicit": False, "reason": "No selected table."}

    intent_name = intent.get("intent", "analysis")
    if intent_name in {"driver_analysis", "feature_selection", "decision_readiness", "definition"}:
        return {"is_explicit": False, "reason": "Intent requires business validation."}

    measures = [item for item in linked_columns if _is_metric_item(item, table_profile)]
    aggregation = _aggregation(question, measures)
    dimensions = []
    for item in linked_columns:
        direct_level = _direct_dimension_level(item, question)
        role = item.get("role")
        is_groupable_role = role in {"dimension", "binary_flag", "datetime"}
        is_direct_identifier = role == "identifier" and direct_level > 0
        is_count_groupable = aggregation == "count" and _is_count_groupable_item(item, table_profile, question)
        has_variation_hint = int(item.get("sample_distinct_count", 0) or 0) > 1 or direct_level > 0
        if (is_groupable_role or is_direct_identifier or is_count_groupable) and _is_query_matched(item) and has_variation_hint:
            dimensions.append(item)
    if not dimensions or (aggregation != "count" and not measures):
        return {
            "is_explicit": False,
            "reason": "The question did not link to both a concrete metric and a concrete dimension.",
        }

    if not (_contains_any(question, EXPLICIT_AGG_TERMS) or intent_name in {"ranking", "comparison", "segmentation", "trend"}):
        return {"is_explicit": False, "reason": "Question does not request aggregation, ranking, comparison, or grouping."}
    if aggregation == "count" and not _contains_any(question, EXPLICIT_AGG_TERMS):
        return {"is_explicit": False, "reason": "Count distribution is handled by distribution evidence."}

    measures.sort(key=lambda item: (_match_score(item), float(item.get("score", 0) or 0)), reverse=True)
    selected_measures = [] if aggregation == "count" else measures[:2]
    filter_candidate_columns = {
        str(item.get("column") or "")
        for item in dimensions
        if _matched_sample_value_filter(item, question)
    }
    dimension_pool = [
        item for item in dimensions
        if str(item.get("column") or "") not in filter_candidate_columns
    ] or dimensions
    selected_dimensions = _select_dimensions(dimension_pool, question)
    selected_filters = _select_filters(linked_columns, selected_measures, selected_dimensions, question)

    confidence = min(
        0.95,
        0.45
        + 0.2 * min(2, len(selected_measures))
        + 0.14 * min(2, len(selected_dimensions))
        + 0.16 * max(_match_score(item) for item in selected_measures + selected_dimensions),
    )
    return {
        "version": "metric-dimension-plan-v1",
        "is_explicit": True,
        "output_type": "metric_dimension_report",
        "intent": intent_name,
        "aggregation": aggregation,
        "measures": selected_measures,
        "dimensions": selected_dimensions,
        "filters": selected_filters,
        "confidence": round(confidence, 2),
        "reason": "The question links explicit metric columns to explicit grouping columns.",
    }
