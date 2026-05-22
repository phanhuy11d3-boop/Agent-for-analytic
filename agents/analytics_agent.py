import json
import re
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from config import GROQ_API_KEY, GROQ_MODEL

SYSTEM_PROMPT = """You are a Senior Data Analyst specializing in fraud detection and retail analytics.
Your job: analyze the provided query results and produce business insights — including CAUSAL analysis, not just correlation.

Return ONLY a valid JSON object (no markdown, no extra text) with this exact structure:
{
  "summary": "2-3 sentences summarizing the key finding",
  "kpis": [
    {"name": "KPI name", "value": "formatted value", "interpretation": "what this means"}
  ],
  "insights": ["insight 1", "insight 2", "insight 3"],
  "anomalies": ["anomaly if any — empty list if none"],
  "recommendation": "One actionable recommendation based on findings",
  "causal_attribution": [
    {
      "factor": "variable name or condition (e.g. 'Mobile device + PayPal in India')",
      "type": "causal",
      "effect": "+X% fraud risk",
      "note": "direct cause after controlling for confounders"
    },
    {
      "factor": "variable that only correlates",
      "type": "confounder",
      "effect": "r=0.X correlation",
      "note": "correlated but causal effect ≈ 0 when controlling for [other variable]"
    }
  ]
}

Guidelines:
- kpis: list 3-5 most important metrics from the data
- insights: use "controlling for X" language where relevant — distinguish what correlates vs. what causes
- Be specific with numbers (percentages, counts, comparisons)
- anomalies: only include if data shows something truly unexpected
- causal_attribution: list 2-4 factors; classify each as "causal" (direct driver) or "confounder" (correlated but not driving)
  * Ask: "If we removed this factor, would fraud rate change?" — if yes, it's causal
  * Ask: "Is this factor just a proxy for another variable?" — if yes, it's a confounder
  * Example causal: "High transaction amount in Electronics → +34% fraud risk, independent of payment method"
  * Example confounder: "Desktop device correlates with low fraud, but this is driven by higher-income demographics, not device itself"
"""


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
        return {
            "summary": text[:500],
            "kpis": [],
            "insights": [text],
            "anomalies": [],
            "recommendation": "",
        }


def _format_data_for_llm(raw_data: list, columns: list) -> str:
    if not raw_data:
        return "No data returned."
    sample = raw_data[:30]
    lines = [", ".join(str(row.get(c, "")) for c in columns) for row in sample]
    header = ", ".join(columns)
    result = f"Columns: {header}\n"
    result += "\n".join(lines)
    if len(raw_data) > 30:
        result += f"\n... ({len(raw_data) - 30} more rows)"
    return result


def run_analytics(raw_data: list, columns: list, question: str, sql_query: str = "") -> dict:
    if not raw_data:
        return {"summary": "No data.", "kpis": [], "insights": [], "anomalies": [], "recommendation": ""}

    data_str = _format_data_for_llm(raw_data, columns)
    llm = ChatGroq(model=GROQ_MODEL, api_key=GROQ_API_KEY, temperature=0.2)

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=(
            f"Original question: {question}\n\n"
            f"SQL used:\n{sql_query}\n\n"
            f"Query results ({len(raw_data)} rows total):\n{data_str}"
        )),
    ]

    response = llm.invoke(messages).content
    return _parse_json_response(response)


def analytics_agent_node(state: dict) -> dict:
    question  = state["question"]
    raw_data  = state.get("raw_data", [])
    columns   = state.get("columns", [])
    sql_query = state.get("sql_query", "")

    if not raw_data:
        step = {
            "agent": "Analytics Agent",
            "icon": "📊",
            "status": "error",
            "message": "No data to analyze — skipping analytics",
        }
        return {
            **state,
            "analytics": None,
            "steps": state.get("steps", []) + [step],
        }

    analytics = run_analytics(raw_data, columns, question, sql_query)

    step = {
        "agent": "Analytics Agent",
        "icon": "📊",
        "status": "success",
        "kpi_count": len(analytics.get("kpis", [])),
        "insight_count": len(analytics.get("insights", [])),
        "message": f"Generated {len(analytics.get('kpis', []))} KPIs and {len(analytics.get('insights', []))} insights",
    }

    return {
        **state,
        "analytics": analytics,
        "steps": state.get("steps", []) + [step],
    }
