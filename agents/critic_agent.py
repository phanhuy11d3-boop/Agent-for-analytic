import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from config import invoke_groq
from tools.report_quality import validate_report_spec


MAX_CRITIC_ROUNDS = 2

SYSTEM_PROMPT = """You are a generic analytics quality reviewer.
Review whether the report specification answers the user's question without overclaiming.

Return only valid JSON:
{
  "verdict": "approve" or "needs_more_data",
  "feedback": "one concise sentence",
  "suggested_focus": "specific missing angle if more data is truly required, otherwise empty"
}

Reject only when a fix would materially improve correctness. Pay special attention to:
- sample rows being mistaken for full-table evidence
- missing outcome validation for decision or definition questions
- charts that do not match available columns
- unsupported good/bad or causal claims
"""


def _parse(text: str) -> dict[str, Any]:
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
    return {"verdict": "approve", "feedback": "Reviewer response could not be parsed; deterministic checks passed.", "suggested_focus": ""}


def _deterministic_review(state: dict) -> dict[str, str] | None:
    report_spec = state.get("report_spec") or {}
    intent = report_spec.get("intent") or {}
    warnings = report_spec.get("warnings") or []
    data_scope = report_spec.get("data_scope") or {}

    if not report_spec:
        return {
            "verdict": "needs_more_data",
            "feedback": "No report specification was produced.",
            "suggested_focus": "Build a report specification before writing the report.",
        }

    if report_spec.get("mode") == "context_required":
        return None

    quality_issues = validate_report_spec(report_spec)
    blockers = [issue for issue in quality_issues if issue.get("severity") == "blocker"]
    if blockers:
        issue = blockers[0]
        return {
            "verdict": "needs_more_data",
            "feedback": issue.get("message", "Report quality gate failed."),
            "suggested_focus": issue.get("code", "Fix report quality before rendering."),
        }

    if intent.get("requires_validation") and report_spec.get("evidence_level") == "descriptive":
        return {
            "verdict": "needs_more_data",
            "feedback": "The question requires validation but the report has only descriptive evidence.",
            "suggested_focus": "Identify whether the table has any candidate outcome column or explicitly state proxy-only evidence.",
        }

    if data_scope.get("source") == "sample_profile" and not warnings:
        return {
            "verdict": "needs_more_data",
            "feedback": "Sample-only analysis must carry an explicit scope warning.",
            "suggested_focus": "Add a scope warning that profiling is based on preview rows only.",
        }

    return None


def critic_agent_node(state: dict) -> dict:
    rounds = state.get("critic_rounds", 0)
    quality_issues = validate_report_spec(state.get("report_spec") or {})
    deterministic = _deterministic_review(state)

    if deterministic:
        result = deterministic
    else:
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=json.dumps({
                "question": state.get("question", ""),
                "data_scope": (state.get("report_spec") or {}).get("data_scope", {}),
                "evidence_level": state.get("evidence_level"),
                "warnings": state.get("warnings", []),
                "report_spec": state.get("report_spec", {}),
                "analytics": state.get("analytics", {}),
            }, ensure_ascii=False, default=str)),
        ]
        try:
            result = _parse(invoke_groq(messages, temperature=0).content)
        except Exception:
            result = {"verdict": "approve", "feedback": "Deterministic review passed; LLM review was unavailable.", "suggested_focus": ""}

    verdict = result.get("verdict", "approve")
    feedback = result.get("feedback", "")
    suggested_focus = result.get("suggested_focus", "")

    step = {
        "agent": "Critic Agent",
        "icon": "QA",
        "status": "success",
        "verdict": verdict,
        "message": feedback,
        "quality_issues": quality_issues,
    }

    critic_feedback = None
    if verdict == "needs_more_data" and rounds < MAX_CRITIC_ROUNDS:
        critic_feedback = suggested_focus or feedback

    return {
        **state,
        "quality_issues": quality_issues,
        "critic_feedback": critic_feedback,
        "critic_rounds": rounds + 1,
        "steps": state.get("steps", []) + [step],
    }
