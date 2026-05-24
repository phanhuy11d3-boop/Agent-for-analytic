import json
import re
from typing import Any

from tools.semantic_inference import best_role


_VALID_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _compact_column(col: dict[str, Any]) -> dict[str, Any]:
    values = []
    for item in col.get("sample_top_values", [])[:3]:
        label = item.get("label")
        if label is not None:
            values.append(str(label))
    return {
        "name": col.get("name", ""),
        "type": col.get("data_type", ""),
        "role": best_role(col),
        "missing_pct": col.get("missing_pct", 0),
        "sample_distinct_count": col.get("sample_distinct_count", 0),
        "sample_values": values,
    }


def _project_row(row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    return {col: row.get(col) for col in columns if col in row}


def build_data_context(
    table_name: str | None,
    table_profile: dict[str, Any],
    raw_data: list[dict[str, Any]] | None = None,
    columns: list[str] | None = None,
) -> dict[str, Any]:
    profile_columns = table_profile.get("columns", [])
    table_columns = columns or [str(col.get("name", "")) for col in profile_columns]
    sample_rows = list(raw_data or table_profile.get("sample_rows") or [])
    valid_table = bool(table_name and _VALID_TABLE_NAME_RE.fullmatch(table_name))

    return {
        "version": "data-context-v1",
        "mode": "single_table",
        "constraints": {
            "single_table_only": True,
            "max_tables_v1": 1,
            "selected_table_required": True,
            "valid_table_identifier": valid_table,
            "llm_sample_token_budget": 2000,
        },
        "tables": [
            {
                "table_name": table_name,
                "row_count": table_profile.get("row_count"),
                "sample_row_count": len(sample_rows) if sample_rows else table_profile.get("sample_row_count", 0),
                "columns_total": table_profile.get("columns_total", len(table_columns)),
                "columns": [_compact_column(col) for col in profile_columns],
                "sample_rows": sample_rows,
            }
        ],
    }


def build_llm_data_context(
    data_context: dict[str, Any],
    focus_columns: list[str] | None = None,
    max_tokens: int = 2000,
) -> dict[str, Any]:
    tables = data_context.get("tables") or []
    if not tables:
        return {"version": "llm-data-context-v1", "tables": [], "truncated": False}

    table = tables[0]
    all_columns = list(table.get("columns") or [])
    focus = [col for col in (focus_columns or []) if col]
    focus_set = set(focus)

    def priority(col: dict[str, Any]) -> tuple[int, float]:
        role = col.get("role", "")
        if col.get("name") in focus_set:
            return (0, 0)
        if role in {"outcome", "measure", "dimension", "datetime"}:
            return (1, float(col.get("missing_pct") or 0))
        if role == "binary_flag":
            return (2, float(col.get("missing_pct") or 0))
        return (3, float(col.get("missing_pct") or 0))

    sorted_columns = sorted(all_columns, key=priority)
    budget_chars = max_tokens * 4

    max_rows = 5
    max_columns = min(30, len(sorted_columns))
    while True:
        selected_columns = sorted_columns[:max_columns]
        selected_names = [col["name"] for col in selected_columns]
        payload = {
            "version": "llm-data-context-v1",
            "mode": data_context.get("mode", "single_table"),
            "constraints": data_context.get("constraints", {}),
            "tables": [
                {
                    "table_name": table.get("table_name"),
                    "row_count": table.get("row_count"),
                    "columns_total": table.get("columns_total"),
                    "included_columns": selected_columns,
                    "omitted_columns": max(0, len(all_columns) - len(selected_columns)),
                    "sample_rows": [
                        _project_row(row, selected_names)
                        for row in list(table.get("sample_rows") or [])[:max_rows]
                    ],
                }
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, default=str)
        if len(raw) <= budget_chars or (max_rows <= 2 and max_columns <= 8):
            payload["truncated"] = len(raw) > budget_chars or len(all_columns) > len(selected_columns)
            return payload
        if max_rows > 2:
            max_rows -= 1
        elif max_columns > 8:
            max_columns -= 4
        else:
            return payload
