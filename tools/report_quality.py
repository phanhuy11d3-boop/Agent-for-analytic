from typing import Any


def validate_report_spec(report_spec: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not report_spec:
        return [{
            "severity": "blocker",
            "code": "missing_report_spec",
            "message": "No report specification was produced.",
        }]

    if report_spec.get("mode") == "context_required":
        return issues

    sections = report_spec.get("sections") or []
    direct_answer = next((section for section in sections if section.get("title") == "Direct Answer"), None)
    if not direct_answer or not str(direct_answer.get("content", "")).strip():
        issues.append({
            "severity": "blocker",
            "code": "missing_direct_answer",
            "message": "Report does not contain a direct answer section.",
        })

    if len(report_spec.get("charts") or []) > 2:
        issues.append({
            "severity": "warning",
            "code": "too_many_charts",
            "message": "V1 report should render no more than two charts.",
        })

    if len(report_spec.get("executive_points") or []) > 5:
        issues.append({
            "severity": "warning",
            "code": "too_many_points",
            "message": "V1 report should contain no more than five executive points.",
        })

    intent = report_spec.get("intent") or {}
    if intent.get("requires_validation") and report_spec.get("evidence_level") in {"descriptive", "limited"}:
        issues.append({
            "severity": "blocker",
            "code": "validation_needed",
            "message": "The question requires validation but evidence is only descriptive.",
        })

    evidence_results = report_spec.get("evidence_results") or []
    if not evidence_results:
        issues.append({
            "severity": "warning",
            "code": "missing_evidence_results",
            "message": "Report has no explicit evidence results attached.",
        })

    for chart in report_spec.get("charts") or []:
        if not chart.get("data"):
            issues.append({
                "severity": "warning",
                "code": "empty_chart_data",
                "message": f"Chart '{chart.get('title', chart.get('id'))}' has no data.",
            })

    return issues
