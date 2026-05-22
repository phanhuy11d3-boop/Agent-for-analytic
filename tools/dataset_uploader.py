import io
import re
import datetime
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME, SQL_TIMEOUT


def infer_pg_type(series: pd.Series) -> str:
    dtype = series.dtype
    if pd.api.types.is_bool_dtype(dtype):
        return "BOOLEAN"
    if pd.api.types.is_integer_dtype(dtype):
        return "BIGINT"
    if pd.api.types.is_float_dtype(dtype):
        return "DOUBLE PRECISION"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "TIMESTAMP"
    if dtype == object:
        sample = series.dropna().head(100)
        if len(sample) > 0:
            try:
                pd.to_datetime(sample)
                return "TIMESTAMP"
            except (ValueError, TypeError):
                pass
            uuid_re = re.compile(
                r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
                re.IGNORECASE,
            )
            if sample.astype(str).str.match(uuid_re).all():
                return "UUID"
        max_len = int(series.dropna().astype(str).str.len().max()) if len(series.dropna()) > 0 else 255
        return f"VARCHAR({min(max_len * 2, 2048)})"
    return "TEXT"


def sanitize_name(name: str) -> str:
    clean = re.sub(r'[^a-z0-9]', '_', name.lower())
    clean = re.sub(r'_+', '_', clean).strip('_')
    return clean or 'col'


def sanitize_table_name(filename: str) -> str:
    stem = Path(filename).stem
    clean = sanitize_name(stem)[:56]
    return f"ds_{clean}"


def sanitize_columns(df: pd.DataFrame) -> pd.DataFrame:
    seen: dict[str, int] = {}
    new_cols = []
    for col in df.columns:
        safe = sanitize_name(str(col))
        if safe in seen:
            seen[safe] += 1
            safe = f"{safe}_{seen[safe]}"
        else:
            seen[safe] = 0
        new_cols.append(safe)
    df.columns = new_cols
    return df


def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = %s",
        (table_name,),
    )
    return cursor.fetchone() is not None


def _unique_table_name(cursor, base_name: str) -> str:
    if not _table_exists(cursor, base_name):
        return base_name
    i = 2
    while _table_exists(cursor, f"{base_name}_{i}"):
        i += 1
    return f"{base_name}_{i}"


def upload_dataframe(df: pd.DataFrame, filename: str) -> dict:
    df = sanitize_columns(df.copy())
    base_name = sanitize_table_name(filename)

    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER,
            password=DB_PASSWORD, dbname=DB_NAME,
            connect_timeout=SQL_TIMEOUT,
        )
        conn.autocommit = False
    except Exception as e:
        return {"success": False, "error": str(e), "table_name": base_name}

    try:
        cursor = conn.cursor()
        table_name = _unique_table_name(cursor, base_name)

        col_defs = ", ".join(
            f"{col} {infer_pg_type(df[col])}" for col in df.columns
        )
        cursor.execute(f'CREATE TABLE "{table_name}" ({col_defs})')

        cols_str = ", ".join(f'"{c}"' for c in df.columns)

        # COPY protocol is 10-50x faster than INSERT VALUES for large datasets.
        # Falls back to batched execute_values if COPY is unsupported (e.g. PgBouncer transaction mode).
        try:
            buf = io.StringIO()
            df.to_csv(buf, index=False, header=False)
            buf.seek(0)
            cursor.copy_expert(
                f"COPY \"{table_name}\" ({cols_str}) FROM STDIN WITH (FORMAT CSV, NULL '')",
                buf,
            )
        except Exception:
            conn.rollback()
            cursor.execute(f'CREATE TABLE "{table_name}" ({col_defs})')
            records = [
                tuple(None if (isinstance(v, float) and pd.isna(v)) else v for v in row)
                for row in df.itertuples(index=False, name=None)
            ]
            BATCH = 5000
            insert_sql = f'INSERT INTO "{table_name}" ({cols_str}) VALUES %s'
            for i in range(0, len(records), BATCH):
                psycopg2.extras.execute_values(cursor, insert_sql, records[i:i + BATCH])

        conn.commit()
        cursor.close()
        conn.close()

        return {
            "success": True,
            "table_name": table_name,
            "rows": len(df),
            "columns": list(df.columns),
        }

    except Exception as e:
        conn.rollback()
        conn.close()
        return {"success": False, "error": str(e), "table_name": base_name}
