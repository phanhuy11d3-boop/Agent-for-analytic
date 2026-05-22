import json
import re
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from config import GROQ_API_KEY, GROQ_MODEL

MAX_CRITIC_ROUNDS = 2

SYSTEM_PROMPT = """You are a Senior Analytics Critic. Your job is to evaluate whether an analytics result
sufficiently answers the original business question, or whether additional data is needed.

Review:
1. Does the SQL query actually capture what the question asks?
2. Are the insights grounded in the data shown (not speculative)?
3. Is there an obvious missing angle — comparison, baseline, time trend, segment breakdown?

Return ONLY a valid JSON object:
{
  "verdict": "approve" or "needs_more_data",
  "feedback": "one concise sentence explaining your decision",
  "suggested_focus": "if needs_more_data: what specific query or angle is missing. Otherwise empty string."
}

Be decisive. Only request more data if it would materially change the conclusion.
Do NOT request more data just to be thorough — approve if the current data is sufficient."""


def _parse(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {"verdict": "approve", "feedback": "Could not parse critic response — proceeding.", "suggested_focus": ""}


def _format_sample(raw_data: list, columns: list) -> str:
    if not raw_data:
        return "No data."
    sample = raw_data[:10]
    header = ", ".join(columns)
    lines = [", ".join(str(row.get(c, "")) for c in columns) for row in sample]
    return f"Columns: {header}\n" + "\n".join(lines) + (f"\n... ({len(raw_data) - 10} more rows)" if len(raw_data) > 10 else "")


def critic_agent_node(state: dict) -> dict:
    question  = state["question"]
    sql_query = state.get("sql_query", "")
    raw_data  = state.get("raw_data", [])
    columns   = state.get("columns", [])
    analytics = state.get("analytics", {}) or {}
    rounds    = state.get("critic_rounds", 0)

    llm = ChatGroq(model=GROQ_MODEL, api_key=GROQ_API_KEY, temperature=0)

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=(
            f"Original question: {question}\n\n"
            f"SQL used:\n{sql_query}\n\n"
            f"Data sample ({len(raw_data)} rows total):\n{_format_sample(raw_data, columns)}\n\n"
            f"Analytics output:\n"
            f"Summary: {analytics.get('summary', '')}\n"
            f"KPIs: {analytics.get('kpis', [])}\n"
            f"Insights: {analytics.get('insights', [])}\n"
            f"Recommendation: {analytics.get('recommendation', '')}"
        )),
    ]

    result = _parse(llm.invoke(messages).content)
    verdict         = result.get("verdict", "approve")
    feedback        = result.get("feedback", "")
    suggested_focus = result.get("suggested_focus", "")

    step = {
        "agent":   "Critic Agent",
        "icon":    "🔍",
        "status":  "success",
        "verdict": verdict,
        "message": feedback,
    }

    critic_feedback = None
    if verdict == "needs_more_data" and rounds < MAX_CRITIC_ROUNDS:
        critic_feedback = suggested_focus or feedback

    return {
        **state,
        "critic_feedback": critic_feedback,
        "critic_rounds":   rounds + 1,
        "steps":           state.get("steps", []) + [step],
    }
