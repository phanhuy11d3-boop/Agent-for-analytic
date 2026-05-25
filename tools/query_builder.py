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


def _filter_conditions(filters: list[dict[str, Any]] | None) -> list[str]:
    conditions: list[str] = []
    for item in filters or []:
        column = item.get("column")
        if not column:
            continue
        value = item.get("value", item.get("label"))
        conditions.append(f"{quote_identifier(str(column))} = {quote_literal(value)}")
    return conditions


def _where_clause(conditions: list[str]) -> str:
    return "WHERE " + " AND ".join(conditions)


def build_preview_query(table_name: str) -> str:
    return f"SELECT *\nFROM {quote_identifier(table_name)}"


def build_distribution_query(
    table_name: str,
    dimension: str,
    limit: int = 10,
    order_by: str = "value_desc",
    filters: list[dict[str, Any]] | None = None,
) -> str:
    table = quote_identifier(table_name)
    dim = quote_identifier(dimension)
    order_clause = "label ASC" if order_by == "label_asc" else "value DESC"
    where_sql = _where_clause([f"{dim} IS NOT NULL", *_filter_conditions(filters)])
    return (
        f"SELECT {dim} AS label, COUNT(*) AS value\n"
        f"FROM {table}\n"
        f"{where_sql}\n"
        f"GROUP BY {dim}\n"
        f"ORDER BY {order_clause}\n"
        f"LIMIT {int(limit)}"
    )


def build_count_by_dimension_query(
    table_name: str,
    dimension: str,
    limit: int = 10,
    filters: list[dict[str, Any]] | None = None,
) -> str:
    table = quote_identifier(table_name)
    dim = quote_identifier(dimension)
    where_sql = _where_clause([f"{dim} IS NOT NULL", *_filter_conditions(filters)])
    return (
        f"SELECT {dim} AS label, COUNT(*) AS row_count, COUNT(*) AS value\n"
        f"FROM {table}\n"
        f"{where_sql}\n"
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
    filters: list[dict[str, Any]] | None = None,
) -> str:
    table = quote_identifier(table_name)
    dim = quote_identifier(dimension)
    met = quote_identifier(metric)
    agg_sql = "AVG" if aggregation.lower() == "avg" else "SUM"
    where_sql = _where_clause([f"{dim} IS NOT NULL", f"{met} IS NOT NULL", *_filter_conditions(filters)])
    return (
        f"SELECT {dim} AS label, COUNT(*) AS row_count, {agg_sql}({met}) AS value\n"
        f"FROM {table}\n"
        f"{where_sql}\n"
        f"GROUP BY {dim}\n"
        f"ORDER BY value DESC\n"
        f"LIMIT {int(limit)}"
    )


def build_metric_by_two_dimensions_query(
    table_name: str,
    primary_dimension: str,
    series_dimension: str,
    metric: str,
    aggregation: str = "sum",
    limit: int = 80,
    filters: list[dict[str, Any]] | None = None,
) -> str:
    table = quote_identifier(table_name)
    primary = quote_identifier(primary_dimension)
    series = quote_identifier(series_dimension)
    met = quote_identifier(metric)
    agg_sql = "AVG" if aggregation.lower() == "avg" else "SUM"
    where_sql = _where_clause([
        f"{primary} IS NOT NULL",
        f"{series} IS NOT NULL",
        f"{met} IS NOT NULL",
        *_filter_conditions(filters),
    ])
    return (
        f"SELECT {primary} AS label, {series} AS series, COUNT(*) AS row_count, {agg_sql}({met}) AS value\n"
        f"FROM {table}\n"
        f"{where_sql}\n"
        f"GROUP BY {primary}, {series}\n"
        f"ORDER BY {primary}, value DESC\n"
        f"LIMIT {int(limit)}"
    )


def build_trend_query(
    table_name: str,
    date_col: str,
    measure_col: str | None = None,
    granularity: str = "month",
    limit: int = 24,
) -> str:
    table = quote_identifier(table_name)
    d = quote_identifier(date_col)
    gran = granularity.lower() if granularity.lower() in {"day", "week", "month", "quarter", "year"} else "month"
    if measure_col:
        m = quote_identifier(measure_col)
        return (
            f"SELECT DATE_TRUNC('{gran}', {d}::timestamp)::date AS period,\n"
            f"  COUNT(*) AS row_count, AVG({m}) AS value\n"
            f"FROM {table}\n"
            f"WHERE {d} IS NOT NULL AND {m} IS NOT NULL\n"
            f"GROUP BY 1\n"
            f"ORDER BY 1\n"
            f"LIMIT {int(limit)}"
        )
    return (
        f"SELECT DATE_TRUNC('{gran}', {d}::timestamp)::date AS period,\n"
        f"  COUNT(*) AS value\n"
        f"FROM {table}\n"
        f"WHERE {d} IS NOT NULL\n"
        f"GROUP BY 1\n"
        f"ORDER BY 1\n"
        f"LIMIT {int(limit)}"
    )


def build_scalar_query(table_name: str, measure_col: str) -> str:
    table = quote_identifier(table_name)
    col = quote_identifier(measure_col)
    return (
        f"SELECT COUNT(*) AS total_rows,\n"
        f"  COUNT({col}) AS non_null_count,\n"
        f"  ROUND(AVG({col})::numeric, 4) AS mean_value,\n"
        f"  MIN({col}) AS min_value,\n"
        f"  MAX({col}) AS max_value\n"
        f"FROM {table}\n"
        f"WHERE {col} IS NOT NULL"
    )


def build_top_n_query(
    table_name: str,
    dimension: str,
    measure_col: str,
    n: int = 10,
    agg: str = "SUM",
) -> str:
    table = quote_identifier(table_name)
    dim = quote_identifier(dimension)
    met = quote_identifier(measure_col)
    agg_sql = "SUM" if agg.upper() == "SUM" else "AVG"
    return (
        f"SELECT {dim} AS label, {agg_sql}({met}) AS value, COUNT(*) AS row_count\n"
        f"FROM {table}\n"
        f"WHERE {dim} IS NOT NULL AND {met} IS NOT NULL\n"
        f"GROUP BY {dim}\n"
        f"ORDER BY value DESC\n"
        f"LIMIT {int(n)}"
    )


def build_outcome_rate_trend_query(
    table_name: str,
    date_col: str,
    outcome_col: str,
    positive_value: Any,
    granularity: str = "month",
    limit: int = 24,
) -> str:
    table = quote_identifier(table_name)
    d = quote_identifier(date_col)
    out = quote_identifier(outcome_col)
    pos = quote_literal(positive_value)
    gran = granularity.lower() if granularity.lower() in {"day", "week", "month", "quarter", "year"} else "month"
    return (
        f"SELECT DATE_TRUNC('{gran}', {d}::timestamp)::date AS period,\n"
        f"  COUNT(*) AS row_count,\n"
        f"  ROUND(AVG(CASE WHEN CAST({out} AS TEXT) = {pos} THEN 1.0 ELSE 0.0 END) * 100, 2) AS value\n"
        f"FROM {table}\n"
        f"WHERE {d} IS NOT NULL AND {out} IS NOT NULL\n"
        f"GROUP BY 1\n"
        f"ORDER BY 1\n"
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
