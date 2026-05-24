from config import SEMANTIC_PROFILE_SAMPLE_LIMIT
from tools.context_store import load_context
from tools.data_context import build_data_context, build_llm_data_context
from tools.intent_classifier import classify_intent
from tools.mschema_builder import build_mschema
from tools.schema_interview import build_context_required_spec, detect_semantic_gaps
from tools.semantic_layer import build_semantic_layer
from tools.table_profiler import profile_from_rows, profile_table
from tools.report_planner import analytics_from_report_spec


def semantic_agent_node(state: dict) -> dict:
    question = state.get("question", "")
    selected_table = state.get("selected_table")
    raw_data = state.get("raw_data", [])
    columns = state.get("columns", [])

    if selected_table:
        table_profile = profile_table(selected_table, sample_limit=SEMANTIC_PROFILE_SAMPLE_LIMIT)
        if not table_profile.get("success"):
            table_profile = profile_from_rows(raw_data, columns)
            table_profile.setdefault("warnings", []).append("Database profiling failed; semantic gate fell back to sample rows.")
    else:
        table_profile = profile_from_rows(raw_data, columns)

    intent = classify_intent(question)
    semantic_context = load_context(selected_table, table_profile)
    mschema = build_mschema(table_profile, semantic_context)
    semantic_layer = build_semantic_layer(table_profile, semantic_context)
    data_context = build_data_context(selected_table, table_profile, raw_data, columns)
    llm_data_context = build_llm_data_context(data_context)
    gap_result = detect_semantic_gaps(question, intent, table_profile, semantic_context)
    clarification_required = bool(gap_result.get("clarification_required"))

    report_spec = None
    analytics = None
    if clarification_required:
        report_spec = build_context_required_spec(
            question=question,
            intent=intent,
            table_profile=table_profile,
            semantic_context=semantic_context,
            mschema=mschema,
            gaps=gap_result.get("gaps", []),
        )
        analytics = analytics_from_report_spec(report_spec)

    step = {
        "agent": "Semantic Agent",
        "icon": "CTX",
        "status": "needs_context" if clarification_required else "success",
        "message": (
            f"Semantic context required: {gap_result.get('blocking_gap_count', 0)} blocking question(s)"
            if clarification_required
            else "Semantic context is sufficient for this question"
        ),
        "semantic_gaps": gap_result.get("gaps", []),
    }

    return {
        **state,
        "table_profile": table_profile,
        "data_context": data_context,
        "llm_data_context": llm_data_context,
        "intent": intent,
        "semantic_context": semantic_context,
        "mschema": mschema,
        "semantic_layer": semantic_layer,
        "semantic_gaps": gap_result.get("gaps", []),
        "clarification_required": clarification_required,
        "report_spec": report_spec or state.get("report_spec"),
        "analytics": analytics or state.get("analytics"),
        "evidence_level": (report_spec or {}).get("evidence_level", state.get("evidence_level")),
        "warnings": (report_spec or {}).get("warnings", state.get("warnings", [])),
        "row_count_total": table_profile.get("row_count"),
        "steps": state.get("steps", []) + [step],
    }
