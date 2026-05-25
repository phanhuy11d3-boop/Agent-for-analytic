from pathlib import Path
from html_report import generate_report


_PROFILE_ONLY_KINDS = {"candidate_scores", "missingness", "count_fallback"}


def writer_agent_node(state: dict) -> dict:
    analytics = state.get("analytics")
    if not analytics:
        step = {
            "agent": "Writer Agent",
            "icon": "✍️",
            "status": "error",
            "message": "No analytics data — cannot generate report",
        }
        return {**state, "report_path": None, "steps": state.get("steps", []) + [step]}

    # Merge evidence_results into report_spec so html_report can use them
    report_spec = state.get("report_spec")
    evidence_results = state.get("evidence_results") or []
    if report_spec is not None:
        if evidence_results and not report_spec.get("evidence_results"):
            report_spec = {**report_spec, "evidence_results": evidence_results}

    # Flag reports with no real evidence so html_report can render a warning banner
    evidence_quality = analytics.get("_evidence_quality")
    is_context_required = (report_spec or {}).get("mode") == "context_required"
    if evidence_quality in ("insufficient", "llm_failed") and not is_context_required:
        anomalies = analytics.get("anomalies") or []
        report_spec = {
            **(report_spec or {}),
            "data_quality_flag": evidence_quality,
            "data_quality_message": anomalies[:3] if anomalies else ["No aggregate evidence was collected."],
        }

    report_path = generate_report(
        question=state.get("question", ""),
        sql_query=state.get("sql_query", ""),
        raw_data=state.get("raw_data", []),
        columns=state.get("columns", []),
        analytics=analytics,
        report_spec=report_spec,
        table_profile=state.get("table_profile"),
    )

    filename = Path(report_path).name
    step = {
        "agent": "Writer Agent",
        "icon": "✍️",
        "status": "success",
        "report_path": report_path,
        "report_filename": filename,
        "message": f"Report saved: {filename}",
    }

    return {
        **state,
        "report_path": report_path,
        "report_filename": filename,
        "final_answer": analytics.get("summary", ""),
        "steps": state.get("steps", []) + [step],
    }
