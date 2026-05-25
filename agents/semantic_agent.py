from config import SEMANTIC_PROFILE_SAMPLE_LIMIT, SEMANTIC_PROPOSAL_USE_LLM
from tools.answerability import evaluate_answerability
from tools.context_store import load_context
from tools.data_context import build_data_context, build_llm_data_context
from tools.intent_classifier import classify_intent
from tools.metric_dimension_planner import build_explicit_aggregate_plan
from tools.mschema_builder import build_mschema
from tools.schema_interview import build_context_required_spec, detect_semantic_gaps
from tools.semantic_proposer import propose_semantic_context
from tools.semantic_layer import build_semantic_layer
from tools.schema_linker import link_schema
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
    semantic_proposal = propose_semantic_context(
        table_profile=table_profile,
        question=question,
        existing_context=semantic_context,
        use_llm=SEMANTIC_PROPOSAL_USE_LLM,
    )
    mschema = build_mschema(table_profile, semantic_context)
    semantic_layer = build_semantic_layer(table_profile, semantic_context)
    data_context = build_data_context(selected_table, table_profile, raw_data, columns)
    llm_data_context = build_llm_data_context(data_context)
    linked_columns = link_schema(question, table_profile, semantic_context, limit=12)
    explicit_aggregate_plan = build_explicit_aggregate_plan(
        question=question,
        intent=intent,
        table_profile=table_profile,
        linked_columns=linked_columns,
    )
    gap_result = detect_semantic_gaps(question, intent, table_profile, semantic_context)
    answerability = evaluate_answerability(
        question=question,
        intent=intent,
        table_profile=table_profile,
        semantic_context=semantic_context,
        semantic_proposal=semantic_proposal,
        explicit_aggregate_plan=explicit_aggregate_plan,
    )
    clarification_required = bool(answerability.get("should_stop"))

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
            semantic_proposal=semantic_proposal,
            answerability=answerability,
        )
        analytics = analytics_from_report_spec(report_spec)

    step = {
        "agent": "Semantic Agent",
        "icon": "CTX",
        "status": "needs_context" if clarification_required else "success",
        "message": (
            f"Semantic output gate: {answerability.get('status')} ({answerability.get('output_type')})"
            if clarification_required
            else f"Semantic output gate: {answerability.get('status')} ({answerability.get('output_type')})"
        ),
        "semantic_gaps": gap_result.get("gaps", []),
        "answerability": answerability,
        "explicit_aggregate_plan": explicit_aggregate_plan,
    }

    return {
        **state,
        "table_profile": table_profile,
        "data_context": data_context,
        "llm_data_context": llm_data_context,
        "intent": intent,
        "semantic_context": semantic_context,
        "semantic_proposal": semantic_proposal,
        "mschema": mschema,
        "semantic_layer": semantic_layer,
        "semantic_gaps": gap_result.get("gaps", []),
        "linked_columns": linked_columns,
        "explicit_aggregate_plan": explicit_aggregate_plan,
        "answerability": answerability,
        "output_type": answerability.get("output_type"),
        "clarification_required": clarification_required,
        "report_spec": report_spec or state.get("report_spec"),
        "analytics": analytics or state.get("analytics"),
        "evidence_level": (report_spec or {}).get("evidence_level", state.get("evidence_level")),
        "warnings": (report_spec or {}).get("warnings", state.get("warnings", [])),
        "row_count_total": table_profile.get("row_count"),
        "steps": state.get("steps", []) + [step],
    }
