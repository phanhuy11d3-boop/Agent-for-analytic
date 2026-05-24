import datetime
import decimal
import copy
import re
import time
from collections import Counter
from typing import Any

from config import (
    DB_HOST,
    DB_NAME,
    DB_PASSWORD,
    DB_PORT,
    DB_USER,
    PROFILE_COLUMN_LIMIT,
    PROFILE_SAMPLE_LIMIT,
    PROFILE_TOP_N,
    SQL_TIMEOUT,
)
from tools.semantic_inference import infer_column_roles, is_binary_like


_VALID_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_NUMERIC_TYPES = {
    "smallint",
    "integer",
    "bigint",
    "decimal",
    "numeric",
    "real",
    "double precision",
}

_DATE_TYPE_MARKERS = ("date", "time")
_PROFILE_CACHE_TTL_SECONDS = 300
_PROFILE_CACHE: dict[tuple[str, int], dict[str, Any]] = {}


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _serialize(value: Any) -> Any:
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


def _connect():
    import psycopg2

    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        dbname=DB_NAME,
        connect_timeout=SQL_TIMEOUT,
    )


def _column_metadata(cursor, table_name: str) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table_name,),
    )
    return [
        {
            "name": row["column_name"],
            "data_type": row["data_type"],
            "is_nullable": row["is_nullable"] == "YES",
        }
        for row in cursor.fetchall()
    ]


def _sample_rows(cursor, table_name: str, columns: list[str], limit: int) -> list[dict[str, Any]]:
    if not columns:
        return []
    col_sql = ", ".join(_quote_identifier(col) for col in columns)
    cursor.execute(f"SELECT {col_sql} FROM {_quote_identifier(table_name)} LIMIT %s", (limit,))
    return [{k: _serialize(v) for k, v in dict(row).items()} for row in cursor.fetchall()]


def _top_values_from_sample(rows: list[dict[str, Any]], col: str) -> list[dict[str, Any]]:
    counts = Counter()
    for row in rows:
        value = row.get(col)
        if value is None or value == "":
            continue
        counts[str(value)] += 1
    return [
        {"label": label, "value": count}
        for label, count in counts.most_common(PROFILE_TOP_N)
    ]


def _full_counts(cursor, table_name: str, columns: list[str]) -> tuple[int, dict[str, int]]:
    expressions = ["COUNT(*) AS __row_count"]
    aliases = {f"__nn_{idx}": col for idx, col in enumerate(columns)}
    expressions.extend(
        f"COUNT({_quote_identifier(col)}) AS {_quote_identifier(alias)}"
        for alias, col in aliases.items()
    )
    cursor.execute(f"SELECT {', '.join(expressions)} FROM {_quote_identifier(table_name)}")
    row = dict(cursor.fetchone())
    total = int(row.pop("__row_count") or 0)
    return total, {col: int(row.get(alias) or 0) for alias, col in aliases.items()}


def _row_count(cursor, table_name: str) -> int:
    cursor.execute(f"SELECT COUNT(*) AS __row_count FROM {_quote_identifier(table_name)}")
    row = dict(cursor.fetchone())
    return int(row.get("__row_count") or 0)


def _estimated_row_count(cursor, table_name: str) -> int:
    cursor.execute(
        """
        SELECT GREATEST(c.reltuples::bigint, 0) AS row_count
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname = %s
        """,
        (table_name,),
    )
    row = cursor.fetchone()
    if not row:
        return 0
    return int(dict(row).get("row_count") or 0)


def invalidate_profile_cache(table_name: str | None = None) -> None:
    if table_name is None:
        _PROFILE_CACHE.clear()
        return
    for key in list(_PROFILE_CACHE):
        if key[0] == table_name:
            _PROFILE_CACHE.pop(key, None)


def _cache_get(table_name: str, sample_limit: int) -> dict[str, Any] | None:
    item = _PROFILE_CACHE.get((table_name, sample_limit))
    if not item:
        return None
    if time.time() - item["ts"] > _PROFILE_CACHE_TTL_SECONDS:
        _PROFILE_CACHE.pop((table_name, sample_limit), None)
        return None
    return copy.deepcopy(item["profile"])


def _cache_put(table_name: str, sample_limit: int, profile: dict[str, Any]) -> dict[str, Any]:
    _PROFILE_CACHE[(table_name, sample_limit)] = {
        "ts": time.time(),
        "profile": copy.deepcopy(profile),
    }
    return profile


def _numeric_stats(cursor, table_name: str, col: str) -> dict[str, Any]:
    qcol = _quote_identifier(col)
    cursor.execute(
        f"""
        SELECT
          MIN({qcol}) AS min_value,
          MAX({qcol}) AS max_value,
          AVG({qcol}) AS mean_value
        FROM {_quote_identifier(table_name)}
        WHERE {qcol} IS NOT NULL
        """
    )
    row = dict(cursor.fetchone())
    return {
        "min": _serialize(row.get("min_value")),
        "max": _serialize(row.get("max_value")),
        "mean": _serialize(row.get("mean_value")),
    }


def _datetime_stats(cursor, table_name: str, col: str) -> dict[str, Any]:
    qcol = _quote_identifier(col)
    cursor.execute(
        f"""
        SELECT
          MIN({qcol}) AS min_value,
          MAX({qcol}) AS max_value
        FROM {_quote_identifier(table_name)}
        WHERE {qcol} IS NOT NULL
        """
    )
    row = dict(cursor.fetchone())
    return {
        "min": _serialize(row.get("min_value")),
        "max": _serialize(row.get("max_value")),
    }


def _numeric_stats_from_sample(values: list[Any]) -> dict[str, Any]:
    numeric_values = []
    for value in values:
        try:
            numeric_values.append(float(value))
        except (TypeError, ValueError):
            continue
    if not numeric_values:
        return {}
    return {
        "min": min(numeric_values),
        "max": max(numeric_values),
        "mean": sum(numeric_values) / len(numeric_values),
        "stats_scope": "sample",
    }


def _datetime_stats_from_sample(values: list[Any]) -> dict[str, Any]:
    clean = sorted(str(value) for value in values if value is not None and str(value).strip() != "")
    if not clean:
        return {}
    return {
        "min": clean[0],
        "max": clean[-1],
        "stats_scope": "sample",
    }


def profile_table(table_name: str, sample_limit: int = PROFILE_SAMPLE_LIMIT) -> dict[str, Any]:
    try:
        import psycopg2.extras
    except ModuleNotFoundError:
        return {
            "success": False,
            "error": "psycopg2 is not installed in the active runtime",
            "table_name": table_name,
            "columns": [],
            "warnings": ["Database profiling could not run because the PostgreSQL driver is unavailable."],
        }

    if not table_name or not _VALID_TABLE_NAME_RE.fullmatch(table_name):
        return {
            "success": False,
            "error": f"Invalid table name: {table_name}",
            "table_name": table_name,
            "columns": [],
            "warnings": ["Could not profile table because its name failed validation."],
        }

    cached = _cache_get(table_name, sample_limit)
    if cached is not None:
        cached.setdefault("warnings", []).append("Table profile was served from the in-process cache.")
        return cached

    warnings: list[str] = []
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute("SET LOCAL statement_timeout = %s", (int(SQL_TIMEOUT * 1000),))
            meta = _column_metadata(cursor, table_name)
            if not meta:
                return {
                    "success": False,
                    "error": f"Table not found: {table_name}",
                    "table_name": table_name,
                    "columns": [],
                    "warnings": ["Selected table was not found in the public schema."],
                }

            deep_profile_names = {col["name"] for col in meta[:PROFILE_COLUMN_LIMIT]}
            if len(meta) > PROFILE_COLUMN_LIMIT:
                warnings.append(
                    f"Deep numeric/date profiling was capped at {PROFILE_COLUMN_LIMIT} columns to protect runtime; all columns still received shallow profiling."
                )
            if meta:
                warnings.append(
                    "Numeric/date min, max, and mean values are computed from the bounded sample to avoid repeated full-table scans."
                )
                warnings.append(
                    "Column missing rates are estimated from the bounded sample; only total row count is computed against the full table."
                )
                warnings.append(
                    "Total row count uses PostgreSQL planner statistics when available to avoid slow COUNT(*) scans."
                )

            profiled_meta = meta
            sample_rows = _sample_rows(cursor, table_name, [col["name"] for col in meta], sample_limit)
            sample_total = len(sample_rows)
            total_rows = _estimated_row_count(cursor, table_name) or sample_total

            columns = []
            for col in profiled_meta:
                name = col["name"]
                sample_values = [
                    row.get(name) for row in sample_rows
                    if row.get(name) is not None and row.get(name) != ""
                ]
                sample_distinct = len({str(v) for v in sample_values})
                sample_non_null = len(sample_values)
                missing_pct = (
                    round(((sample_total - sample_non_null) / sample_total * 100), 2)
                    if sample_total
                    else 0
                )
                non_null = (
                    int(round(total_rows * sample_non_null / sample_total))
                    if sample_total
                    else 0
                )
                profile = {
                    "name": name,
                    "data_type": col["data_type"],
                    "is_nullable": col["is_nullable"],
                    "row_count": total_rows,
                    "non_null_count": non_null,
                    "missing_count": max(total_rows - non_null, 0),
                    "missing_pct": missing_pct,
                    "profile_scope": "sample_estimate",
                    "sample_distinct_count": sample_distinct,
                    "sample_top_values": _top_values_from_sample(sample_rows, name),
                    "is_binary_like": is_binary_like(sample_values),
                }

                if name in deep_profile_names:
                    dtype = col["data_type"].lower()
                    if dtype in _NUMERIC_TYPES:
                        profile.update(_numeric_stats_from_sample(sample_values))
                    elif any(marker in dtype for marker in _DATE_TYPE_MARKERS):
                        profile.update(_datetime_stats_from_sample(sample_values))

                profile["role_candidates"] = infer_column_roles({
                    **profile,
                    "distinct_count": sample_distinct,
                })
                columns.append(profile)

    role_counts = Counter()
    for col in columns:
        if col.get("role_candidates"):
            role_counts[col["role_candidates"][0]["role"]] += 1

    result = {
        "success": True,
        "table_name": table_name,
        "row_count": total_rows,
        "sample_row_count": len(sample_rows),
        "sample_limit": sample_limit,
        "columns": columns,
        "columns_total": len(meta),
        "columns_profiled": len(columns),
        "columns_deep_profiled": min(len(meta), PROFILE_COLUMN_LIMIT),
        "role_counts": dict(role_counts),
        "sample_rows": sample_rows,
        "warnings": warnings,
    }
    return _cache_put(table_name, sample_limit, result)


def profile_from_rows(rows: list[dict[str, Any]], columns: list[str]) -> dict[str, Any]:
    total_rows = len(rows)
    profiled = []
    for name in columns:
        values = [row.get(name) for row in rows]
        non_null_values = [v for v in values if v is not None and v != ""]
        sample_distinct = len({str(v) for v in non_null_values})
        profile = {
            "name": name,
            "data_type": "sample",
            "is_nullable": True,
            "row_count": total_rows,
            "non_null_count": len(non_null_values),
            "missing_count": total_rows - len(non_null_values),
            "missing_pct": round(((total_rows - len(non_null_values)) / total_rows * 100), 2) if total_rows else 0,
            "sample_distinct_count": sample_distinct,
            "sample_top_values": _top_values_from_sample(rows, name),
            "is_binary_like": is_binary_like(non_null_values),
        }

        numeric_values = []
        for value in non_null_values:
            try:
                numeric_values.append(float(value))
            except (TypeError, ValueError):
                pass
        if numeric_values and len(numeric_values) == len(non_null_values):
            profile.update({
                "data_type": "numeric_sample",
                "min": min(numeric_values),
                "max": max(numeric_values),
                "mean": sum(numeric_values) / len(numeric_values),
            })

        profile["role_candidates"] = infer_column_roles({
            **profile,
            "distinct_count": sample_distinct,
        })
        profiled.append(profile)

    return {
        "success": True,
        "table_name": None,
        "row_count": total_rows,
        "sample_row_count": total_rows,
        "sample_limit": total_rows,
        "columns": profiled,
        "columns_total": len(columns),
        "columns_profiled": len(profiled),
        "columns_deep_profiled": len(profiled),
        "role_counts": dict(Counter(
            col["role_candidates"][0]["role"]
            for col in profiled
            if col.get("role_candidates")
        )),
        "sample_rows": rows,
        "warnings": ["Only sample rows were available, so profile statistics may not represent the full table."],
    }
