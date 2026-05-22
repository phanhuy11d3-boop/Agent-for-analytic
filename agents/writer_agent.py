from pathlib import Path
from html_report import generate_report


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

    report_path = generate_report(
        question  = state.get("question", ""),
        sql_query = state.get("sql_query", ""),
        raw_data  = state.get("raw_data", []),
        columns   = state.get("columns", []),
        analytics = analytics,
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
        "report_path":     report_path,
        "report_filename": filename,
        "final_answer":    analytics.get("summary", ""),
        "steps":           state.get("steps", []) + [step],
    }
