import psycopg2
from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME, SQL_TIMEOUT

_schema_cache: str | None = None


def get_schema() -> str:
    global _schema_cache
    if _schema_cache is not None:
        return _schema_cache

    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            dbname=DB_NAME,
            connect_timeout=SQL_TIMEOUT,
        )
        cursor = conn.cursor()

        cursor.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        tables = [row[0] for row in cursor.fetchall()]

        if not tables:
            cursor.close()
            conn.close()
            return "No tables found in the database."

        parts = []
        for table in tables:
            cursor.execute("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = %s
                ORDER BY ordinal_position
            """, (table,))
            columns = cursor.fetchall()

            cursor.execute("""
                SELECT COUNT(*) FROM """ + f'"{table}"')
            row_count = cursor.fetchone()[0]

            col_lines = "\n".join(
                f"  - {col} ({dtype}{', nullable' if nullable == 'YES' else ''})"
                for col, dtype, nullable in columns
            )
            parts.append(f"Table: {table} ({row_count:,} rows)\nColumns:\n{col_lines}")

        cursor.close()
        conn.close()

        _schema_cache = "\n\n".join(parts)
        return _schema_cache

    except Exception as e:
        return f"Could not retrieve schema: {e}"


def invalidate_schema_cache() -> None:
    global _schema_cache
    _schema_cache = None


def get_schema_for_table(table_name: str) -> str:
    """Return schema string for a single table (bypasses global cache)."""
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER,
            password=DB_PASSWORD, dbname=DB_NAME,
            connect_timeout=SQL_TIMEOUT,
        )
        cursor = conn.cursor()

        cursor.execute("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
        """, (table_name,))
        columns = cursor.fetchall()

        if not columns:
            cursor.close()
            conn.close()
            return f"Table '{table_name}' not found in database."

        cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
        row_count = cursor.fetchone()[0]

        col_lines = "\n".join(
            f"  - {col} ({dtype}{', nullable' if nullable == 'YES' else ''})"
            for col, dtype, nullable in columns
        )
        cursor.close()
        conn.close()
        return f"Table: {table_name} ({row_count:,} rows)\nColumns:\n{col_lines}"

    except Exception as e:
        return f"Could not retrieve schema for '{table_name}': {e}"
