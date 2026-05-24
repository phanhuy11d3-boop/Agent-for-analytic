import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from config import invoke_groq
from tools.data_context import build_data_context, build_llm_data_context
from tools.evidence_planner import build_evidence_plan, execute_evidence_plan
from tools.intent_classifier import classify_intent
from tools.report_planner import analytics_from_report_spec, build_report_spec
from tools.schema_linker import link_schema
from tools.table_profiler import profile_from_rows, profile_table


SYSTEM_PROMPT = """You are a senior data analyst for arbitrary uploaded tables.
You receive a structured report specification produced by deterministic code.

Return only a valid JSON object with this exact shape:
{
  "summary": "2-3 concise sentences grounded in the report specification",
  "kpis": [
    {"name": "KPI name", "value": "formatted value", "interpretation": "what this means"}
  ],
  "insights": ["insight 1", "insight 2", "insight 3"],
  "anomalies": ["warning or limitation if any"],
  "recommendation": "Finding -> So What -> Now What",
  "hypotheses": []
}

Rules:
- Do not invent business meaning that is not present in the specification.
- Do not claim a row, entity, or segment is good or bad unless the specification says the evidence is validated.
- Distinguish full-table profile statistics from bounded sample rows.
- Treat aggregate/profile evidence as the source of truth; sample rows are only for data-shape context.
- Do not choose new charts, filters, or metrics. They are already selected in the report specification.
- If evidence is proxy-based, say candidate/proxy rather than proven.
- Never claim causation from this report.
"""


def _parse_json_response(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {}


def _normalise_analytics(value: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    result = fallback.copy()
    if not isinstance(value, dict):
        return result

    for key in ("summary", "recommendation"):
        if isinstance(value.get(key), str) and value[key].strip():
            result[key] = value[key].strip()
    for key in ("kpis", "insights", "anomalies", "hypotheses"):
        if isinstance(value.get(key), list):
            result[key] = value[key]
    return result


def run_analytics(
    raw_data: list,
    columns: list,
    question: str,
    sql_query: str = "",
    table_profile: dict[str, Any] | None = None,
    intent: dict[str, Any] | None = None,
    report_spec: dict[str, Any] | None = None,
    llm_data_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    table_profile = table_profile or profile_from_rows(raw_data, columns)
    intent = intent or classify_intent(question)
    report_spec = report_spec or build_report_spec(
        question=question,
        intent=intent,
        table_profile=table_profile,
        raw_row_count=len(raw_data),
        semantic_context=None,
        mschema=None,
    )

    fallback = analytics_from_report_spec(report_spec)

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=json.dumps({
            "question": question,
            "sql_used_for_preview": sql_query,
            "llm_data_context": llm_data_context or {},
            "report_spec": report_spec,
        }, ensure_ascii=False, default=str)),
    ]

    try:
        response = invoke_groq(messages, temperature=0.2).content
        return _normalise_analytics(_parse_json_response(response), fallback)
    except Exception:
        return fallback


def analytics_agent_node(state: dict) -> dict:
    question = state["question"]
    raw_data = state.get("raw_data", [])
    columns = state.get("columns", [])
    sql_query = state.get("sql_query", "")
    selected_table = state.get("selected_table")

    if not raw_data:
        step = {
            "agent": "Analytics Agent",
            "icon": "AN",
            "status": "error",
            "message": "No preview rows were returned, so analytics was skipped",
        }
        return {
            **state,
            "analytics": None,
            "steps": state.get("steps", []) + [step],
        }

    if state.get("table_profile"):
        table_profile = state["table_profile"]
    elif selected_table:
        table_profile = profile_table(selected_table)
        if not table_profile.get("success"):
            table_profile = profile_from_rows(raw_data, columns)
            table_profile["warnings"].append("Database profiling failed, so the report fell back to preview rows.")
    else:
        table_profile = profile_from_rows(raw_data, columns)

    intent = state.get("intent") or classify_intent(question)
    data_context = state.get("data_context") or build_data_context(
        selected_table,
        table_profile,
        raw_data,
        columns,
    )
    linked_columns = link_schema(
        question=question,
        table_profile=table_profile,
        semantic_context=state.get("semantic_context"),
        limit=12,
    )
    llm_data_context = build_llm_data_context(
        data_context,
        focus_columns=[item.get("column", "") for item in linked_columns[:12]],
    )
    evidence_plan = build_evidence_plan(
        question=question,
        intent=intent,
        table_profile=table_profile,
        semantic_context=state.get("semantic_context"),
        linked_columns=linked_columns,
    )
    evidence_results = execute_evidence_plan(evidence_plan)
    report_spec = build_report_spec(
        question=question,
        intent=intent,
        table_profile=table_profile,
        raw_row_count=len(raw_data),
        semantic_context=state.get("semantic_context"),
        mschema=state.get("mschema"),
        data_context=data_context,
        linked_columns=linked_columns,
        evidence_plan=evidence_plan,
        evidence_results=evidence_results,
    )
    analytics = run_analytics(
        raw_data=raw_data,
        columns=columns,
        question=question,
        sql_query=sql_query,
        table_profile=table_profile,
        intent=intent,
        report_spec=report_spec,
        llm_data_context=llm_data_context,
    )

    step = {
        "agent": "Analytics Agent",
        "icon": "AN",
        "status": "success",
        "message": (
            f"Built {report_spec.get('version')} with "
            f"{table_profile.get('row_count', len(raw_data)):,} profiled rows and "
            f"{len(evidence_results)} evidence item(s)"
        ),
    }

    return {
        **state,
        "analytics": analytics,
        "table_profile": table_profile,
        "data_context": data_context,
        "llm_data_context": llm_data_context,
        "intent": intent,
        "linked_columns": linked_columns,
        "evidence_plan": evidence_plan,
        "evidence_results": evidence_results,
        "report_spec": report_spec,
        "evidence_level": report_spec.get("evidence_level"),
        "warnings": report_spec.get("warnings", []),
        "row_count_total": table_profile.get("row_count"),
        "steps": state.get("steps", []) + [step],
    }
