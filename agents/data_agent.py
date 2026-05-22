import re
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from config import GROQ_API_KEY, GROQ_MODEL
from tools.sql_executor import execute_sql
from tools.schema_provider import get_schema, get_schema_for_table

SYSTEM_PROMPT = """You are a Data Engineer specializing in PostgreSQL analytics.
Your ONLY job: convert a business question into a valid PostgreSQL SELECT query.

{schema}

STRICT RULES:
- Return ONLY the raw SQL query — no markdown, no ```sql, no explanation
- Only use SELECT statements
- Use exact table and column names from the schema above
- For rate/percentage: ROUND(AVG(flag_col) * 100, 2) AS rate_pct
- For counts: COUNT(*) AS total_count
- Always include ORDER BY when ranking results
- Do not add LIMIT unless the question specifically asks for top-N
"""


def _clean_sql(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:sql)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    return text


def data_agent_node(state: dict) -> dict:
    question        = state["question"]
    critic_feedback = state.get("critic_feedback")
    selected_table  = state.get("selected_table")
    schema          = get_schema_for_table(selected_table) if selected_table else get_schema()

    llm = ChatGroq(model=GROQ_MODEL, api_key=GROQ_API_KEY, temperature=0)

    user_content = f"Question: {question}"
    if critic_feedback:
        user_content += f"\n\nCritic feedback on previous query: {critic_feedback}\nGenerate a new SQL query that addresses this gap."

    messages = [
        SystemMessage(content=SYSTEM_PROMPT.format(schema=schema)),
        HumanMessage(content=user_content),
    ]

    sql = _clean_sql(llm.invoke(messages).content)

    result = execute_sql(sql)

    step = {
        "agent": "Data Agent",
        "icon": "🗄️",
        "status": "success" if result["success"] else "error",
        "sql": sql,
        "rows": result.get("row_count", 0),
        "message": f"Executed SQL — {result.get('row_count', 0)} rows returned"
                   if result["success"]
                   else f"SQL error: {result.get('error')}",
    }

    retry_result = None
    if not result["success"]:
        # One automatic retry with error context
        retry_messages = messages + [
            SystemMessage(content=f"Previous SQL failed with error: {result['error']}\nFix the SQL and return only the corrected query."),
        ]
        sql2 = _clean_sql(llm.invoke(retry_messages).content)
        retry_result = execute_sql(sql2)
        if retry_result["success"]:
            sql = sql2
            result = retry_result
            step["sql"] = sql
            step["status"] = "success"
            step["rows"] = result["row_count"]
            step["message"] = f"Retry succeeded — {result['row_count']} rows returned"

    return {
        **state,
        "sql_query":  sql,
        "raw_data":   result.get("data", []),
        "columns":    result.get("columns", []),
        "data_error": None if result["success"] else result.get("error"),
        "steps":      state.get("steps", []) + [step],
    }
