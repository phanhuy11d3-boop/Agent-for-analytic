import re
from typing import Any

from config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER, QUERY_SAMPLE_LIMIT, SQL_TIMEOUT


BLOCKED_KEYWORDS = ["INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE"]
_LIMIT_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)


def _blocked_error(query: str) -> str | None:
    query_upper = query.upper()
    for keyword in BLOCKED_KEYWORDS:
        if re.search(rf"\b{keyword}\b", query_upper):
            return f"Blocked: {keyword} statements are not allowed"
    if not re.match(r"^\s*(SELECT|WITH)\b", query, re.IGNORECASE):
        return "Blocked: only SELECT statements are allowed"
    return None


def _with_limit(query: str, row_limit: int | None) -> str:
    cleaned = query.strip().rstrip(";")
    if row_limit is None or _LIMIT_RE.search(cleaned):
        return cleaned
    return f"{cleaned} LIMIT {int(row_limit)}"


def execute_read_sql(query: str, row_limit: int | None = None, purpose: str = "query") -> dict[str, Any]:
    query = query.strip()
    blocked = _blocked_error(query)
    if blocked:
        return {"success": False, "error": blocked, "query": query, "purpose": purpose}

    final_query = _with_limit(query, row_limit)

    try:
        import psycopg2
        import psycopg2.extras

        with psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            dbname=DB_NAME,
            connect_timeout=SQL_TIMEOUT,
        ) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("SET LOCAL statement_timeout = %s", (int(SQL_TIMEOUT * 1000),))
                cursor.execute(final_query)
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
        return {
            "success": True,
            "data": [dict(row) for row in rows],
            "columns": columns,
            "row_count": len(rows),
            "query": final_query,
            "row_limit": row_limit,
            "purpose": purpose,
            "is_sample": row_limit is not None,
        }
    except Exception as e:
        return {"success": False, "error": str(e), "query": final_query, "purpose": purpose}


def execute_sample_sql(query: str, row_limit: int = QUERY_SAMPLE_LIMIT) -> dict[str, Any]:
    return execute_read_sql(query, row_limit=row_limit, purpose="sample")


def execute_aggregate_sql(query: str) -> dict[str, Any]:
    return execute_read_sql(query, row_limit=None, purpose="aggregate")


def execute_sql(query: str) -> dict[str, Any]:
    """Backward-compatible sample executor for existing callers."""
    return execute_sample_sql(query)
