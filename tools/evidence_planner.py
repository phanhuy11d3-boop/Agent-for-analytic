from typing import Any

from tools.query_builder import (
    build_distribution_query,
    build_metric_by_dimension_query,
    build_numeric_summary_query,
    build_outcome_distribution_query,
    build_outcome_rate_by_dimension_query,
)
from tools.sql_executor import execute_aggregate_sql


def _fmt_label(name: str) -> str:
    return str(name or "").replace("_", " ").title()


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


def build_evidence_plan(
    question: str,
    intent: dict[str, Any],
    table_profile: dict[str, Any],
    semantic_context: dict[str, Any] | None,
    linked_columns: list[dict[str, Any]],
    max_sql_items: int = 3,
) -> dict[str, Any]:
    context = semantic_context or {}
    table_name = table_profile.get("table_name")
    items: list[dict[str, Any]] = []

    score_item = _candidate_score_item(linked_columns)
    if score_item:
        items.append(score_item)

    missing_item = _missingness_item(linked_columns)
    if missing_item:
        items.append(missing_item)

    if not table_name:
        return {
            "version": "evidence-plan-v1",
            "question": question,
            "items": items,
            "warnings": ["Only static profile evidence is available because no selected table was provided."],
        }

    dimension = _find_any(linked_columns, {"dimension", "binary_flag"})
    measure = _find_column(linked_columns, "measure")
    confirmed_metric = context.get("primary_metric")
    if confirmed_metric:
        measure = {"column": confirmed_metric, "role": "measure", "score": 1.0}
    outcome = context.get("outcome_column")
    positive = context.get("positive_outcome_value")

    sql_items: list[dict[str, Any]] = []
    if outcome and positive and dimension and dimension.get("column") != outcome:
        sql_items.append({
            "id": f"outcome_rate_by_{dimension['column']}",
            "kind": "outcome_rate_by_dimension",
            "title": f"Outcome rate by {_fmt_label(dimension['column'])}",
            "description": "Validated only if the saved positive outcome value is correct.",
            "sql": build_outcome_rate_by_dimension_query(table_name, dimension["column"], outcome, positive),
            "unit": "%",
        })
        sql_items.append({
            "id": f"outcome_distribution_{outcome}",
            "kind": "distribution",
            "title": f"Outcome distribution - {_fmt_label(outcome)}",
            "description": "Distribution of the confirmed outcome column.",
            "sql": build_outcome_distribution_query(table_name, outcome),
            "unit": "rows",
        })
    elif dimension and measure and dimension.get("column") != measure.get("column"):
        sql_items.append({
            "id": f"avg_{measure['column']}_by_{dimension['column']}",
            "kind": "metric_by_dimension",
            "title": f"Average {_fmt_label(measure['column'])} by {_fmt_label(dimension['column'])}",
            "description": "Grouped aggregate selected from linked metric and dimension fields.",
            "sql": build_metric_by_dimension_query(table_name, dimension["column"], measure["column"], "avg"),
            "unit": "avg",
        })
    elif dimension:
        sql_items.append({
            "id": f"distribution_{dimension['column']}",
            "kind": "distribution",
            "title": f"Distribution - {_fmt_label(dimension['column'])}",
            "description": "Top categories for the most relevant grouping field.",
            "sql": build_distribution_query(table_name, dimension["column"]),
            "unit": "rows",
        })

    if measure:
        sql_items.append({
            "id": f"summary_{measure['column']}",
            "kind": "numeric_summary",
            "title": f"Numeric summary - {_fmt_label(measure['column'])}",
            "description": "Basic numeric aggregate for the most relevant measure.",
            "sql": build_numeric_summary_query(table_name, measure["column"]),
            "unit": "value",
        })

    items.extend(sql_items[:max_sql_items])
    return {
        "version": "evidence-plan-v1",
        "question": question,
        "items": items[: max_sql_items + 2],
        "warnings": [],
    }


def execute_evidence_plan(evidence_plan: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in evidence_plan.get("items", []):
        result = {
            "id": item.get("id"),
            "kind": item.get("kind"),
            "title": item.get("title"),
            "description": item.get("description"),
            "sql": item.get("sql", ""),
            "unit": item.get("unit", ""),
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
        results.append(result)
    return results
