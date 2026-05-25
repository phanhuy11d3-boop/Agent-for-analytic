import re
import unicodedata
from typing import Any

from tools.query_builder import (
    build_count_by_dimension_query,
    build_distribution_query,
    build_metric_by_dimension_query,
    build_metric_by_two_dimensions_query,
    build_numeric_summary_query,
    build_outcome_distribution_query,
    build_outcome_rate_by_dimension_query,
    build_outcome_rate_trend_query,
    build_scalar_query,
    build_top_n_query,
    build_trend_query,
)
from tools.schema_provider import get_columns_for_table
from tools.sql_executor import execute_aggregate_sql


DISTRIBUTION_TERMS = {
    "distribution",
    "frequency",
    "frequencies",
    "histogram",
    "count by",
    "counts by",
    "number of",
    "phan bo",
    "tan suat",
}


def _normalise(text: Any) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _fmt_label(name: str) -> str:
    return str(name or "").replace("_", " ").title()


def _filter_suffix(filters: list[dict[str, Any]]) -> str:
    parts = [
        f"{_fmt_label(str(item.get('column') or ''))} = {item.get('label', item.get('value'))}"
        for item in filters or []
        if item.get("column")
    ]
    return f" filtered to {', '.join(parts)}" if parts else ""


def _find_column(linked_columns: list[dict[str, Any]], role: str) -> dict[str, Any] | None:
    for col in linked_columns:
        if col.get("role") == role:
            return col
    return None


def _find_any(linked_columns: list[dict[str, Any]], roles: set[str]) -> dict[str, Any] | None:
    for col in linked_columns:
        if col.get("role") in roles:
            return col
    return None


def _direct_link_score(col: dict[str, Any]) -> float:
    return max(
        float(col.get("name_score", 0) or 0),
        float(col.get("value_score", 0) or 0),
        float(col.get("context_score", 0) or 0),
    )


def _concept_names(col: dict[str, Any]) -> set[str]:
    return {str(match.get("concept") or "") for match in col.get("concepts", [])}


def _safe_measure(col: dict[str, Any] | None, confirmed_metric: bool = False) -> bool:
    if not col:
        return False
    if confirmed_metric:
        return True
    return _direct_link_score(col) >= 0.12


def _safe_dimension(col: dict[str, Any] | None) -> bool:
    if not col:
        return False
    # A grouping field should be named, described, or value-matched by the question.
    # Concept-only siblings such as region/state/city are handled by the explicit
    # aggregate planner, not by this fallback path.
    return _direct_link_score(col) >= 0.12


def _distribution_requested(question: str) -> bool:
    normalized = _normalise(question)
    return any(_normalise(term) and _normalise(term) in normalized for term in DISTRIBUTION_TERMS)


def _column_profile(table_profile: dict[str, Any], column: str) -> dict[str, Any]:
    return next((col for col in table_profile.get("columns", []) if col.get("name") == column), {})


def _is_numeric_profile(profile: dict[str, Any]) -> bool:
    text = str(profile.get("data_type") or "").lower()
    return any(token in text for token in ("int", "numeric", "double", "real", "decimal", "float"))


def _find_distribution_column(
    question: str,
    table_profile: dict[str, Any],
    linked_columns: list[dict[str, Any]],
) -> tuple[dict[str, Any], bool] | tuple[None, bool]:
    if not _distribution_requested(question):
        return None, False

    candidates = []
    for col in linked_columns:
        if _direct_link_score(col) < 0.12:
            continue
        role = col.get("role")
        if role not in {"dimension", "binary_flag", "measure"}:
            continue
        distinct = int(col.get("sample_distinct_count", 0) or 0)
        if distinct <= 1:
            continue
        profile = _column_profile(table_profile, str(col.get("column") or ""))
        is_numeric = _is_numeric_profile(profile)
        if role == "measure" and distinct > 120:
            continue
        if role in {"dimension", "binary_flag"} and distinct > 200:
            continue
        candidates.append((
            _direct_link_score(col),
            1 if is_numeric else 0,
            -distinct,
            col,
            is_numeric,
        ))
    if not candidates:
        return None, False
    candidates.sort(reverse=True, key=lambda item: item[:3])
    return candidates[0][3], candidates[0][4]


def _build_distribution_item(table_name: str, column: dict[str, Any], numeric_order: bool) -> dict[str, Any]:
    dim = column["column"]
    limit = 80 if numeric_order else 50
    return {
        "id": f"distribution_{dim}",
        "kind": "distribution",
        "title": f"Frequency Distribution - {_fmt_label(dim)}",
        "description": "Frequency count for the requested field.",
        "sql": build_distribution_query(
            table_name,
            dim,
            limit=limit,
            order_by="label_asc" if numeric_order else "value_desc",
        ),
        "unit": "rows",
        "primary_dimension": dim,
        "distribution_order": "label_asc" if numeric_order else "value_desc",
        "limit": limit,
    }


def _find_safe_column(
    linked_columns: list[dict[str, Any]],
    role: str,
    *,
    confirmed_metric: bool = False,
) -> dict[str, Any] | None:
    for col in linked_columns:
        if col.get("role") != role:
            continue
        if role == "measure" and _safe_measure(col, confirmed_metric=confirmed_metric):
            return col
        if role != "measure" and _safe_dimension(col):
            return col
    return None


def _find_safe_any(linked_columns: list[dict[str, Any]], roles: set[str]) -> dict[str, Any] | None:
    for col in linked_columns:
        if col.get("role") in roles and _safe_dimension(col):
            return col
    return None


def _find_datetime(linked_columns: list[dict[str, Any]]) -> dict[str, Any] | None:
    return _find_column(linked_columns, "datetime")


def _candidate_score_item(linked_columns: list[dict[str, Any]]) -> dict[str, Any] | None:
    data = [
        {
            "label": item.get("column", ""),
            "value": round(float(item.get("score", 0) or 0) * 100, 1),
            "role": item.get("role", ""),
        }
        for item in linked_columns[:5]
    ]
    if not data:
        return None
    return {
        "id": "candidate_score",
        "kind": "candidate_scores",
        "title": "Question-linked candidate fields",
        "description": "Generic schema-linking score, not model feature importance.",
        "data": data,
        "scope": "profile",
    }


def _missingness_item(linked_columns: list[dict[str, Any]]) -> dict[str, Any] | None:
    data = [
        {
            "label": item.get("column", ""),
            "value": round(float(item.get("missing_pct", 0) or 0), 2),
            "role": item.get("role", ""),
        }
        for item in linked_columns[:5]
        if float(item.get("missing_pct", 0) or 0) > 0
    ]
    if not data:
        return None
    return {
        "id": "candidate_missingness",
        "kind": "missingness",
        "title": "Data quality for candidate fields",
        "description": "Missing-value rate among the fields used in the answer.",
        "data": data,
        "scope": "profile",
    }


def _build_trend_items(
    table_name: str,
    date_col: dict,
    measure: dict | None,
    outcome: str | None,
    positive: Any,
) -> list[dict[str, Any]]:
    items = []
    d = date_col["column"]
    if outcome and positive:
        items.append({
            "id": f"trend_{outcome}_rate",
            "kind": "trend",
            "title": f"{_fmt_label(outcome)} rate over time",
            "description": "Trend of outcome rate grouped by month.",
            "sql": build_outcome_rate_trend_query(table_name, d, outcome, positive),
            "unit": "%",
            "x_label": "Period",
            "y_label": "Rate (%)",
        })
    elif measure:
        m = measure["column"]
        items.append({
            "id": f"trend_{m}",
            "kind": "trend",
            "title": f"{_fmt_label(m)} over time",
            "description": "Trend of measure grouped by month.",
            "sql": build_trend_query(table_name, d, m),
            "unit": "avg",
            "x_label": "Period",
            "y_label": _fmt_label(m),
        })
    else:
        items.append({
            "id": f"trend_count",
            "kind": "trend",
            "title": "Record count over time",
            "description": "Volume trend grouped by month.",
            "sql": build_trend_query(table_name, d),
            "unit": "rows",
            "x_label": "Period",
            "y_label": "Count",
        })
    return items


def _build_ranking_items(
    table_name: str,
    dimension: dict,
    measure: dict | None,
    outcome: str | None,
    positive: Any,
) -> list[dict[str, Any]]:
    items = []
    dim = dimension["column"]
    if outcome and positive and dim != outcome:
        items.append({
            "id": f"outcome_rate_by_{dim}",
            "kind": "outcome_rate_by_dimension",
            "title": f"{_fmt_label(outcome)} rate by {_fmt_label(dim)}",
            "description": "Ranked by outcome rate descending.",
            "sql": build_outcome_rate_by_dimension_query(table_name, dim, outcome, positive),
            "unit": "%",
        })
    elif measure and dim != measure["column"]:
        items.append({
            "id": f"top_{dim}_by_{measure['column']}",
            "kind": "top_n",
            "title": f"Top {_fmt_label(dim)} by {_fmt_label(measure['column'])}",
            "description": "Top categories ranked by measure descending.",
            "sql": build_top_n_query(table_name, dim, measure["column"]),
            "unit": "value",
        })
    else:
        items.append({
            "id": f"distribution_{dim}",
            "kind": "distribution",
            "title": f"Distribution - {_fmt_label(dim)}",
            "description": "Top categories by count.",
            "sql": build_distribution_query(table_name, dim),
            "unit": "rows",
        })
    return items


def _build_comparison_items(
    table_name: str,
    dimension: dict | None,
    measure: dict | None,
    outcome: str | None,
    positive: Any,
) -> list[dict[str, Any]]:
    items = []
    if not dimension:
        return items
    dim = dimension["column"]
    if outcome and positive and dim != outcome:
        items.append({
            "id": f"outcome_rate_by_{dim}",
            "kind": "outcome_rate_by_dimension",
            "title": f"{_fmt_label(outcome)} rate by {_fmt_label(dim)}",
            "description": "Validated only if the saved positive outcome value is correct.",
            "sql": build_outcome_rate_by_dimension_query(table_name, dim, outcome, positive),
            "unit": "%",
        })
        items.append({
            "id": f"outcome_dist_{outcome}",
            "kind": "distribution",
            "title": f"Outcome distribution - {_fmt_label(outcome)}",
            "description": "Distribution of confirmed outcome column.",
            "sql": build_outcome_distribution_query(table_name, outcome),
            "unit": "rows",
        })
    elif measure and dim != measure["column"]:
        items.append({
            "id": f"avg_{measure['column']}_by_{dim}",
            "kind": "metric_by_dimension",
            "title": f"Average {_fmt_label(measure['column'])} by {_fmt_label(dim)}",
            "description": "Grouped aggregate.",
            "sql": build_metric_by_dimension_query(table_name, dim, measure["column"], "avg"),
            "unit": "avg",
        })
    else:
        items.append({
            "id": f"distribution_{dim}",
            "kind": "distribution",
            "title": f"Distribution - {_fmt_label(dim)}",
            "description": "Top categories by count.",
            "sql": build_distribution_query(table_name, dim),
            "unit": "rows",
        })
    return items


def _build_scalar_items(
    table_name: str,
    measure: dict | None,
    dimension: dict | None,
    outcome: str | None,
    positive: Any,
) -> list[dict[str, Any]]:
    items = []
    if measure:
        items.append({
            "id": f"scalar_{measure['column']}",
            "kind": "numeric_summary",
            "title": f"Summary - {_fmt_label(measure['column'])}",
            "description": "Aggregate summary of the primary measure.",
            "sql": build_scalar_query(table_name, measure["column"]),
            "unit": "value",
        })
    if outcome and positive and dimension and dimension["column"] != outcome:
        items.append({
            "id": f"outcome_rate_by_{dimension['column']}",
            "kind": "outcome_rate_by_dimension",
            "title": f"{_fmt_label(outcome)} rate by {_fmt_label(dimension['column'])}",
            "description": "Context for scalar answer.",
            "sql": build_outcome_rate_by_dimension_query(
                table_name, dimension["column"], outcome, positive
            ),
            "unit": "%",
        })
    elif not measure and dimension:
        items.append({
            "id": f"distribution_{dimension['column']}",
            "kind": "distribution",
            "title": f"Distribution - {_fmt_label(dimension['column'])}",
            "description": "Count per category.",
            "sql": build_distribution_query(table_name, dimension["column"]),
            "unit": "rows",
        })
    return items


def build_evidence_plan(
    question: str,
    intent: dict[str, Any],
    table_profile: dict[str, Any],
    semantic_context: dict[str, Any] | None,
    linked_columns: list[dict[str, Any]],
    explicit_aggregate_plan: dict[str, Any] | None = None,
    max_sql_items: int = 4,
) -> dict[str, Any]:
    context = semantic_context or {}
    table_name = table_profile.get("table_name")
    items: list[dict[str, Any]] = []
    explicit_plan = explicit_aggregate_plan or {}

    score_item = _candidate_score_item(linked_columns)
    if score_item:
        items.append(score_item)

    missing_item = _missingness_item(linked_columns)
    if missing_item:
        items.append(missing_item)

    if not table_name:
        return {
            "version": "evidence-plan-v2",
            "question": question,
            "intent": intent.get("intent"),
            "table_name": None,
            "items": items,
            "warnings": ["Only static profile evidence is available — no table selected."],
        }

    if explicit_plan.get("is_explicit"):
        dims = explicit_plan.get("dimensions") or []
        measures = explicit_plan.get("measures") or []
        filters = explicit_plan.get("filters") or []
        filter_suffix = _filter_suffix(filters)
        aggregation = explicit_plan.get("aggregation") or "sum"
        sql_items: list[dict[str, Any]] = []
        primary_dim = dims[0] if dims else None
        series_dim = dims[1] if len(dims) > 1 else None
        if aggregation == "count" and primary_dim:
            sql = build_count_by_dimension_query(
                table_name,
                primary_dim["column"],
                filters=filters,
            )
            sql_items.append({
                "id": f"count_rows_by_{primary_dim['column']}",
                "kind": "metric_by_dimension",
                "title": f"Count Rows by {_fmt_label(primary_dim['column'])}{filter_suffix}",
                "description": "Grouped row count answering the requested ranking.",
                "sql": sql,
                "unit": "rows",
                "metric": "rows",
                "aggregation": "count",
                "primary_dimension": primary_dim.get("column"),
                "series_dimension": "",
                "filters": filters,
            })
            return {
                "version": "evidence-plan-v3",
                "question": question,
                "intent": intent.get("intent"),
                "table_name": table_name,
                "explicit_aggregate_plan": explicit_plan,
                "items": sql_items[:max_sql_items],
                "warnings": [],
            }
        for measure in measures[:2]:
            metric = measure.get("column")
            if not metric or not primary_dim:
                continue
            if series_dim:
                sql = build_metric_by_two_dimensions_query(
                    table_name,
                    primary_dim["column"],
                    series_dim["column"],
                    metric,
                    aggregation,
                    filters=filters,
                )
                kind = "metric_by_two_dimensions"
                title = f"{aggregation.title()} {_fmt_label(metric)} by {_fmt_label(primary_dim['column'])} and {_fmt_label(series_dim['column'])}{filter_suffix}"
                description = "Grouped aggregate answering the requested metric/dimension split."
            else:
                sql = build_metric_by_dimension_query(
                    table_name,
                    primary_dim["column"],
                    metric,
                    aggregation,
                    filters=filters,
                )
                kind = "metric_by_dimension"
                title = f"{aggregation.title()} {_fmt_label(metric)} by {_fmt_label(primary_dim['column'])}{filter_suffix}"
                description = "Grouped aggregate answering the requested metric ranking."
            sql_items.append({
                "id": f"{aggregation}_{metric}_by_{primary_dim['column']}" + (f"_{series_dim['column']}" if series_dim else ""),
                "kind": kind,
                "title": title,
                "description": description,
                "sql": sql,
                "unit": aggregation,
                "metric": metric,
                "aggregation": aggregation,
                "primary_dimension": primary_dim.get("column"),
                "series_dimension": series_dim.get("column") if series_dim else "",
                "filters": filters,
            })
        return {
            "version": "evidence-plan-v3",
            "question": question,
            "intent": intent.get("intent"),
            "table_name": table_name,
            "explicit_aggregate_plan": explicit_plan,
            "items": sql_items[:max_sql_items],
            "warnings": [],
        }

    distribution_column, numeric_distribution = _find_distribution_column(question, table_profile, linked_columns)
    if distribution_column:
        return {
            "version": "evidence-plan-v3",
            "question": question,
            "intent": intent.get("intent"),
            "table_name": table_name,
            "items": [_build_distribution_item(table_name, distribution_column, numeric_distribution)],
            "warnings": [],
        }

    # Resolve columns from context + linked columns
    dimension = _find_safe_any(linked_columns, {"dimension", "binary_flag"})
    measure = _find_safe_column(linked_columns, "measure")
    date_col = _find_datetime(linked_columns)
    confirmed_metric = context.get("primary_metric")
    if confirmed_metric:
        measure = {"column": confirmed_metric, "role": "measure", "score": 1.0}
    outcome = context.get("outcome_column")
    positive = context.get("positive_outcome_value")

    # TRUST-SQL: verify resolved columns actually exist in schema before building SQL
    warnings: list[str] = []
    actual_cols = set(get_columns_for_table(table_name) or [])
    if actual_cols:
        if measure and measure.get("column") not in actual_cols:
            warnings.append(
                f"Linked measure column '{measure['column']}' not found in '{table_name}' schema — skipped."
            )
            measure = None
        if dimension and dimension.get("column") not in actual_cols:
            warnings.append(
                f"Linked dimension column '{dimension['column']}' not found in '{table_name}' schema — skipped."
            )
            dimension = None
        if date_col and date_col.get("column") not in actual_cols:
            date_col = None

    intent_name = intent.get("intent", "segmentation")
    sql_items: list[dict[str, Any]] = []

    # Route evidence by intent
    if intent_name == "trend" and date_col:
        sql_items = _build_trend_items(table_name, date_col, measure, outcome, positive)
        # Add comparison context if we have dimension
        if dimension and len(sql_items) < max_sql_items:
            sql_items += _build_comparison_items(table_name, dimension, measure, outcome, positive)[:1]
        # Add numeric summary so KPI block has real values
        if measure and len(sql_items) < max_sql_items:
            sql_items.append({
                "id": f"summary_{measure['column']}",
                "kind": "numeric_summary",
                "title": f"Summary - {_fmt_label(measure['column'])}",
                "description": "Numeric context for trend analysis.",
                "sql": build_numeric_summary_query(table_name, measure["column"]),
                "unit": "value",
            })

    elif intent_name == "ranking":
        if dimension:
            sql_items = _build_ranking_items(table_name, dimension, measure, outcome, positive)
        if dimension and measure and len(sql_items) < max_sql_items:
            sql_items.append({
                "id": f"summary_{measure['column']}",
                "kind": "numeric_summary",
                "title": f"Summary - {_fmt_label(measure['column'])}",
                "description": "Numeric aggregate for context.",
                "sql": build_numeric_summary_query(table_name, measure["column"]),
                "unit": "value",
            })

    elif intent_name in ("definition", "data_quality"):
        # For these, missingness + distribution is most useful
        if dimension:
            sql_items.append({
                "id": f"distribution_{dimension['column']}",
                "kind": "distribution",
                "title": f"Distribution - {_fmt_label(dimension['column'])}",
                "description": "Data quality overview.",
                "sql": build_distribution_query(table_name, dimension["column"]),
                "unit": "rows",
            })
        if measure:
            sql_items.append({
                "id": f"summary_{measure['column']}",
                "kind": "numeric_summary",
                "title": f"Numeric summary - {_fmt_label(measure['column'])}",
                "description": "Basic stats for data quality check.",
                "sql": build_numeric_summary_query(table_name, measure["column"]),
                "unit": "value",
            })

    elif intent_name in ("driver_analysis", "feature_selection", "decision_readiness"):
        # Needs outcome + comparison context
        sql_items = _build_comparison_items(table_name, dimension, measure, outcome, positive)
        if measure and len(sql_items) < max_sql_items:
            sql_items.append({
                "id": f"summary_{measure['column']}",
                "kind": "numeric_summary",
                "title": f"Summary - {_fmt_label(measure['column'])}",
                "description": "Numeric context for driver analysis.",
                "sql": build_numeric_summary_query(table_name, measure["column"]),
                "unit": "value",
            })

    else:
        # segmentation, comparison, anomaly, default
        sql_items = _build_comparison_items(table_name, dimension, measure, outcome, positive)
        if measure and len(sql_items) < max_sql_items:
            sql_items.append({
                "id": f"summary_{measure['column']}",
                "kind": "numeric_summary",
                "title": f"Summary - {_fmt_label(measure['column'])}",
                "description": "Basic numeric aggregate.",
                "sql": build_numeric_summary_query(table_name, measure["column"]),
                "unit": "value",
            })

    # Enrich v2 items with resolved column metadata so _rebuild_simplified_sql can use them
    for _item in sql_items:
        item_kind = _item.get("kind", "")
        # trend items use date_col not categorical dimension — don't mislabel them
        if "primary_dimension" not in _item and dimension and item_kind not in ("trend", "outcome_rate_trend"):
            _item["primary_dimension"] = dimension.get("column", "")
        if "metric" not in _item and measure:
            _item["metric"] = measure.get("column", "")
        if "aggregation" not in _item:
            _item["aggregation"] = "sum"

    items.extend(sql_items[:max_sql_items])
    if not sql_items and intent_name in {"ranking", "comparison", "segmentation", "trend"}:
        warnings.append(
            "No aggregate SQL was generated because the question did not safely link to both the required metric and grouping/date fields."
        )
    return {
        "version": "evidence-plan-v2",
        "question": question,
        "intent": intent_name,
        "table_name": table_name,
        "items": items[: max_sql_items + 2],
        "warnings": warnings,
    }


def _rebuild_simplified_sql(item: dict[str, Any], table_name: str, error_msg: str) -> str | None:
    """MAC-SQL Refiner: rebuild a simpler SQL from stored item metadata when the original fails.

    Uses the kind + primary_dimension + metric already stored on the item to construct
    a less-complex query — no LLM call, no guessing, just simpler params to query_builder.
    Returns None if we can't safely rebuild (e.g. column-not-found errors where the column
    name itself is wrong — no point retrying with the same bad name).
    """
    kind = item.get("kind", "")
    error_lower = (error_msg or "").lower()
    dim = item.get("primary_dimension", "")
    metric = item.get("metric", "")
    agg = item.get("aggregation", "sum")

    # Column-not-found: the column name itself is wrong — rebuilding with same name won't help
    if "does not exist" in error_lower and "column" in error_lower:
        return None

    # Type / cast error: aggregation incompatible with column type → downgrade to COUNT
    is_type_error = any(t in error_lower for t in ("type", "cannot cast", "operator does not exist"))
    fallback_agg = "count" if is_type_error and agg in ("sum", "avg") else agg

    if kind in ("metric_by_dimension", "metric_by_two_dimensions") and dim and agg == "count":
        return build_count_by_dimension_query(table_name, dim, filters=item.get("filters") or [])

    if kind in ("metric_by_dimension", "metric_by_two_dimensions") and dim and metric:
        return build_metric_by_dimension_query(table_name, dim, metric, fallback_agg)

    if kind in ("top_n", "outcome_rate_by_dimension") and dim:
        return build_distribution_query(table_name, dim, limit=10)

    if kind == "distribution" and dim:
        return build_distribution_query(table_name, dim, limit=10)

    if kind == "numeric_summary" and metric:
        # Simplest possible: just count non-null rows for this column
        return f'SELECT COUNT(*) AS row_count, COUNT(DISTINCT "{metric}") AS distinct_count FROM "{table_name}" WHERE "{metric}" IS NOT NULL'

    return None


def execute_evidence_plan(evidence_plan: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    table_name = evidence_plan.get("table_name")
    for item in evidence_plan.get("items", []):
        result = {
            "id": item.get("id"),
            "kind": item.get("kind"),
            "title": item.get("title"),
            "description": item.get("description"),
            "sql": item.get("sql", ""),
            "unit": item.get("unit", ""),
            "x_label": item.get("x_label", ""),
            "y_label": item.get("y_label", ""),
            "metric": item.get("metric", ""),
            "aggregation": item.get("aggregation", ""),
            "primary_dimension": item.get("primary_dimension", ""),
            "series_dimension": item.get("series_dimension", ""),
            "scope": item.get("scope", "full_table_aggregate" if item.get("sql") else "profile"),
            "success": True,
            "data": item.get("data", []),
        }
        if item.get("sql"):
            executed = execute_aggregate_sql(item["sql"])
            result["success"] = bool(executed.get("success"))
            result["data"] = executed.get("data", [])
            result["error"] = executed.get("error")
            result["executed_sql"] = executed.get("query", item["sql"])

            # MAC-SQL Refiner: try kind-aware SQL rebuild before falling back to COUNT(*)
            if not result["success"] and table_name:
                rebuilt_sql = _rebuild_simplified_sql(item, table_name, result.get("error", ""))
                if rebuilt_sql:
                    rebuilt = execute_aggregate_sql(rebuilt_sql)
                    if rebuilt.get("success") and rebuilt.get("data"):
                        result["success"] = True
                        result["data"] = rebuilt["data"]
                        result["executed_sql"] = rebuilt_sql
                        result["_rebuilt"] = True
                        result["error"] = None

            # COUNT(*) accessibility check — last resort, does NOT count as real evidence
            if not result["success"] and table_name:
                fallback_sql = f'SELECT COUNT(*) AS total_rows FROM "{table_name}"'
                retry = execute_aggregate_sql(fallback_sql)
                if retry.get("success") and retry.get("data"):
                    result["success"] = True
                    result["data"] = retry.get("data", [])
                    result["kind"] = "count_fallback"
                    result["executed_sql"] = fallback_sql
                    result["_retried"] = True
                    result["error"] = None
                    result["title"] = (result.get("title") or "") + " [fallback: row count only]"
                else:
                    result["error"] = (
                        f"Original: {result['error']} | Retry COUNT(*): {retry.get('error')}"
                    )
        results.append(result)
    return results
