from typing import Any

from tools.semantic_inference import best_role


def _sample_values(col: dict[str, Any], limit: int = 5) -> list[str]:
    values = []
    for item in col.get("sample_top_values", [])[:limit]:
        label = item.get("label")
        if label is not None:
            values.append(str(label))
    return values


def build_mschema(table_profile: dict[str, Any], semantic_context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = semantic_context or {}
    descriptions = context.get("column_descriptions") or {}
    outcome_column = context.get("outcome_column") or ""
    columns = []

    for col in table_profile.get("columns", []):
        name = col.get("name", "")
        role = best_role(col)
        status = "confirmed" if name in descriptions or name == outcome_column else "inferred"
        columns.append({
            "name": name,
            "data_type": col.get("data_type"),
            "semantic_role": role,
            "confidence": (col.get("role_candidates") or [{}])[0].get("confidence", 0),
            "description": descriptions.get(name, ""),
            "status": status,
            "missing_pct": col.get("missing_pct", 0),
            "sample_distinct_count": col.get("sample_distinct_count", 0),
            "sample_values": _sample_values(col),
            "is_outcome": name == outcome_column,
        })

    return {
        "table_name": table_profile.get("table_name") or context.get("table_name"),
        "table_purpose": context.get("table_purpose", ""),
        "row_grain": context.get("row_grain", ""),
        "primary_metric": context.get("primary_metric", ""),
        "outcome_column": outcome_column,
        "positive_outcome_value": context.get("positive_outcome_value", ""),
        "negative_outcome_value": context.get("negative_outcome_value", ""),
        "columns": columns,
    }
