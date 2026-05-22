import re
import psycopg2
import psycopg2.extras
from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME, SQL_ROW_LIMIT, SQL_TIMEOUT

BLOCKED_KEYWORDS = ["INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE"]


def execute_sql(query: str) -> dict:
    query = query.strip()
    query_upper = query.upper()

    for keyword in BLOCKED_KEYWORDS:
        if re.search(rf'\b{keyword}\b', query_upper):
            return {"success": False, "error": f"Blocked: {keyword} statements are not allowed"}

    if "LIMIT" not in query_upper:
        query = query.rstrip(";") + f" LIMIT {SQL_ROW_LIMIT}"

    try:
        with psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            dbname=DB_NAME,
            connect_timeout=SQL_TIMEOUT,
        ) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(query)
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
        return {
            "success": True,
            "data": [dict(row) for row in rows],
            "columns": columns,
            "row_count": len(rows),
            "query": query,
        }
    except Exception as e:
        return {"success": False, "error": str(e), "query": query}
