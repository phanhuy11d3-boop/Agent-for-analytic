import io

import pandas as pd
import psycopg2
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER, SQL_TIMEOUT
from tools.dataset_uploader import upload_dataframe
from tools.table_profiler import invalidate_profile_cache
from tools.schema_provider import invalidate_schema_cache

router = APIRouter(prefix="/api")

ALLOWED_CONTENT_TYPES = {
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/octet-stream",
}
MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB


@router.post("/upload")
async def upload_dataset(file: UploadFile = File(...)):
    name_lower = (file.filename or "").lower()
    is_csv   = name_lower.endswith(".csv")
    is_excel = name_lower.endswith(".xlsx") or name_lower.endswith(".xls")
    if not (is_csv or is_excel):
        raise HTTPException(400, "Only .csv, .xlsx, .xls files are supported")

    contents = await file.read()
    if len(contents) > MAX_FILE_BYTES:
        raise HTTPException(413, "File exceeds 50 MB limit")

    def _parse():
        if is_csv:
            return pd.read_csv(io.BytesIO(contents))
        return pd.read_excel(io.BytesIO(contents), sheet_name=0)

    try:
        df = await run_in_threadpool(_parse)
    except Exception as e:
        raise HTTPException(422, f"Could not parse file: {e}")

    if df.empty:
        raise HTTPException(422, "File contains no data rows")
    if len(df.columns) > 200:
        raise HTTPException(422, "File has too many columns (max 200)")

    try:
        result = await run_in_threadpool(upload_dataframe, df, file.filename or "upload")
    except Exception as e:
        raise HTTPException(500, f"Database error: {e}")
    if not result["success"]:
        raise HTTPException(500, f"Database error: {result['error']}")

    invalidate_schema_cache()
    invalidate_profile_cache()

    return {
        "table_name": result["table_name"],
        "rows":       result["rows"],
        "columns":    result["columns"],
        "message":    f"Uploaded {result['rows']:,} rows → table '{result['table_name']}'",
    }


@router.get("/tables")
async def list_tables():
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER,
            password=DB_PASSWORD, dbname=DB_NAME,
            connect_timeout=SQL_TIMEOUT,
        )
        cursor = conn.cursor()
        
        # Optimize: Get all tables and estimated row counts in one fast query using pg_class
        cursor.execute("""
            SELECT c.relname AS table_name, c.reltuples::bigint AS row_count
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' 
              AND c.relkind = 'r'
            ORDER BY c.relname
        """)
        rows = cursor.fetchall()

        tables = []
        for row in rows:
            tname = row[0]
            count = row[1]
            # Fallback to exact COUNT(*) if PostgreSQL stats are uninitialized or empty
            if count <= 0:
                try:
                    cursor.execute(f'SELECT COUNT(*) FROM "{tname}"')
                    count = cursor.fetchone()[0]
                except Exception:
                    count = 0
            tables.append({
                "name":        tname,
                "row_count":   count,
                "is_uploaded": tname.startswith("ds_"),
            })

        cursor.close()
        conn.close()
        return {"tables": tables}

    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/tables/{table_name}")
async def delete_table(table_name: str):
    if not table_name.startswith("ds_"):
        raise HTTPException(403, "Only uploaded tables (ds_ prefix) can be deleted")

    import re
    if not re.match(r'^[a-z0-9_]+$', table_name):
        raise HTTPException(400, "Invalid table name")

    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER,
            password=DB_PASSWORD, dbname=DB_NAME,
            connect_timeout=SQL_TIMEOUT,
        )
        conn.autocommit = True
        cursor = conn.cursor()
        cursor.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        cursor.close()
        conn.close()
        invalidate_schema_cache()
        invalidate_profile_cache(table_name)
        return {"message": f"Table '{table_name}' deleted"}
    except Exception as e:
        raise HTTPException(500, str(e))
