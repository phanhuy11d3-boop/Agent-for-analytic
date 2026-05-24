import re

from langchain_core.messages import HumanMessage, SystemMessage

from config import invoke_groq
from tools.data_context import build_data_context, build_llm_data_context
from tools.intent_classifier import classify_intent
from tools.schema_provider import get_columns_for_table, get_schema, get_schema_for_table
from tools.sql_executor import execute_sample_sql
from tools.query_builder import build_preview_query, is_valid_identifier
from tools.table_profiler import profile_from_rows


SYSTEM_PROMPT = """You are a PostgreSQL analytics query generator.
Your only job is to convert a business question into a valid read-only SELECT query.

{schema}

STRICT RULES:
- Return only the raw SQL query. No markdown, no explanation.
- Only use SELECT or WITH statements.
- Use exact table and column names from the schema above.
- Always wrap every table and column identifier in double quotes.
- If a selected table is provided, use only that table.
- Do not invent business concepts or columns that are not present in the schema.
- For percentages on binary 0/1 columns, use ROUND(AVG("col") * 100, 2) AS rate_pct.
- For counts, use COUNT(*) AS total_count.
- Include GROUP BY when selecting non-aggregated columns with aggregate functions.
- Include ORDER BY when ranking results.
- Prefer simple, auditable SQL. The executor applies a bounded sample limit for row-level preview queries.
"""

_VALID_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SQL_IDENTIFIER = r'"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*'
_TABLE_REF_RE = re.compile(
    rf"\b(?:FROM|JOIN)\s+({_SQL_IDENTIFIER}(?:\s*\.\s*{_SQL_IDENTIFIER})?)",
    re.IGNORECASE,
)
_CTE_RE = re.compile(rf"(?:WITH|,)\s+({_SQL_IDENTIFIER})\s+AS\s*\(", re.IGNORECASE)
_NEUTRAL_PREVIEW_INTENTS = {"definition", "feature_selection", "decision_readiness"}


def _clean_sql(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:sql)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    return text


def _normalize_identifier(identifier: str) -> str:
    identifier = identifier.strip()
    if "." in identifier:
        identifier = identifier.split(".")[-1].strip()
    if identifier.startswith('"') and identifier.endswith('"'):
        identifier = identifier[1:-1].replace('""', '"')
    return identifier.lower()


def _referenced_tables(sql: str) -> set[str]:
    ctes = {_normalize_identifier(match.group(1)) for match in _CTE_RE.finditer(sql)}
    tables = set()
    for match in _TABLE_REF_RE.finditer(sql):
        table = _normalize_identifier(match.group(1))
        if table not in ctes:
            tables.add(table)
    return tables


def _selected_table_error(sql: str, selected_table: str | None) -> str | None:
    if not selected_table:
        return None
    if not _VALID_TABLE_NAME_RE.fullmatch(selected_table):
        return f"Invalid selected table name: {selected_table}"

    allowed = selected_table.lower()
    referenced = _referenced_tables(sql)
    if not referenced:
        return f"Query must reference selected table '{selected_table}'"
    if referenced != {allowed}:
        found = ", ".join(sorted(referenced))
        return f"Query must use only selected table '{selected_table}', but referenced: {found}"
    return None


def _execute_checked(sql: str, selected_table: str | None) -> dict:
    validation_error = _selected_table_error(sql, selected_table)
    if validation_error:
        return {"success": False, "error": validation_error, "query": sql}
    return execute_sample_sql(sql)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _neutral_preview_sql(selected_table: str | None) -> str | None:
    if not selected_table or not _VALID_TABLE_NAME_RE.fullmatch(selected_table):
        return None
    return f"SELECT *\nFROM {_quote(selected_table)}"


def _quote_known_identifiers(sql: str, selected_table: str | None, columns: list[str]) -> str:
    if not selected_table or not columns:
        return sql

    identifier_map = {selected_table.lower(): selected_table}
    identifier_map.update({col.lower(): col for col in columns})

    out = []
    i = 0
    while i < len(sql):
        ch = sql[i]

        if ch == "'":
            start = i
            i += 1
            while i < len(sql):
                if sql[i] == "'":
                    i += 1
                    if i < len(sql) and sql[i] == "'":
                        i += 1
                        continue
                    break
                i += 1
            out.append(sql[start:i])
            continue

        if ch == '"':
            start = i
            i += 1
            while i < len(sql):
                if sql[i] == '"':
                    i += 1
                    if i < len(sql) and sql[i] == '"':
                        i += 1
                        continue
                    break
                i += 1
            out.append(sql[start:i])
            continue

        if ch.isalpha() or ch == "_":
            start = i
            i += 1
            while i < len(sql) and (sql[i].isalnum() or sql[i] in {"_", "$"}):
                i += 1
            token = sql[start:i]
            exact = identifier_map.get(token.lower())
            out.append(_quote(exact) if exact else token)
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def data_agent_node(state: dict) -> dict:
    selected_table = state.get("selected_table")
    if not selected_table:
        step = {
            "agent": "Data Agent",
            "icon": "DB",
            "status": "error",
            "message": "Select exactly one dataset/table before running V1 analysis.",
        }
        return {
            **state,
            "data_error": "V1 requires one selected table.",
            "steps": state.get("steps", []) + [step],
        }

    if not is_valid_identifier(selected_table):
        step = {
            "agent": "Data Agent",
            "icon": "DB",
            "status": "error",
            "message": f"Invalid selected table name: {selected_table}",
        }
        return {
            **state,
            "data_error": f"Invalid selected table name: {selected_table}",
            "steps": state.get("steps", []) + [step],
        }

    sql = build_preview_query(selected_table)
    result = _execute_checked(sql, selected_table)
    if result["success"]:
        sql = result.get("query", sql)

    step = {
        "agent": "Data Agent",
        "icon": "DB",
        "status": "success" if result["success"] else "error",
        "sql": sql,
        "rows": result.get("row_count", 0),
        "message": (
            f"Executed preview query - {result.get('row_count', 0)} rows returned"
            if result["success"]
            else f"SQL error: {result.get('error')}"
        ),
    }

    table_profile = state.get("table_profile")
    if not table_profile and result["success"]:
        table_profile = profile_from_rows(result.get("data", []), result.get("columns", []))
    data_context = state.get("data_context")
    llm_data_context = state.get("llm_data_context")
    if result["success"] and table_profile:
        data_context = build_data_context(
            selected_table,
            table_profile,
            result.get("data", []),
            result.get("columns", []),
        )
        llm_data_context = build_llm_data_context(data_context)

    return {
        **state,
        "sql_query": sql,
        "raw_data": result.get("data", []),
        "columns": result.get("columns", []),
        "data_context": data_context,
        "llm_data_context": llm_data_context,
        "data_error": None if result["success"] else result.get("error"),
        "steps": state.get("steps", []) + [step],
    }
