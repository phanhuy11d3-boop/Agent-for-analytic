import re
from typing import Any


_VALID_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_valid_identifier(name: str | None) -> bool:
    return bool(name and _VALID_IDENTIFIER_RE.fullmatch(name))


def quote_identifier(name: str) -> str:
    if not is_valid_identifier(name):
        raise ValueError(f"Invalid SQL identifier: {name}")
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_preview_query(table_name: str) -> str:
    return f"SELECT *\nFROM {quote_identifier(table_name)}"


def build_distribution_query(table_name: str, dimension: str, limit: int = 10) -> str:
    table = quote_identifier(table_name)
    dim = quote_identifier(dimension)
    return (
        f"SELECT {dim} AS label, COUNT(*) AS value\n"
        f"FROM {table}\n"
        f"WHERE {dim} IS NOT NULL\n"
        f"GROUP BY {dim}\n"
        f"ORDER BY value DESC\n"
        f"LIMIT {int(limit)}"
    )


def build_numeric_summary_query(table_name: str, metric: str) -> str:
    table = quote_identifier(table_name)
    col = quote_identifier(metric)
    return (
        "SELECT\n"
        f"  COUNT({col}) AS non_null_count,\n"
        f"  AVG({col}) AS avg_value,\n"
        f"  MIN({col}) AS min_value,\n"
        f"  MAX({col}) AS max_value\n"
        f"FROM {table}\n"
        f"WHERE {col} IS NOT NULL"
    )


def build_metric_by_dimension_query(
    table_name: str,
    dimension: str,
    metric: str,
    aggregation: str = "avg",
    limit: int = 10,
) -> str:
    table = quote_identifier(table_name)
    dim = quote_identifier(dimension)
    met = quote_identifier(metric)
    agg_sql = "AVG" if aggregation.lower() == "avg" else "SUM"
    return (
        f"SELECT {dim} AS label, COUNT(*) AS row_count, {agg_sql}({met}) AS value\n"
        f"FROM {table}\n"
        f"WHERE {dim} IS NOT NULL AND {met} IS NOT NULL\n"
        f"GROUP BY {dim}\n"
        f"ORDER BY value DESC\n"
        f"LIMIT {int(limit)}"
    )


def build_outcome_distribution_query(table_name: str, outcome_column: str, limit: int = 10) -> str:
    return build_distribution_query(table_name, outcome_column, limit=limit)


def build_outcome_rate_by_dimension_query(
    table_name: str,
    dimension: str,
    outcome_column: str,
    positive_value: Any,
    limit: int = 10,
) -> str:
    table = quote_identifier(table_name)
    dim = quote_identifier(dimension)
    out = quote_identifier(outcome_column)
    pos = quote_literal(positive_value)
    return (
        f"SELECT {dim} AS label, COUNT(*) AS row_count,\n"
        f"  ROUND(AVG(CASE WHEN CAST({out} AS TEXT) = {pos} THEN 1.0 ELSE 0.0 END) * 100, 2) AS value\n"
        f"FROM {table}\n"
        f"WHERE {dim} IS NOT NULL AND {out} IS NOT NULL\n"
        f"GROUP BY {dim}\n"
        f"ORDER BY value DESC\n"
        f"LIMIT {int(limit)}"
    )
