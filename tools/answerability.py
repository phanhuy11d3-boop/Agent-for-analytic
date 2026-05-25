import re
import unicodedata
from typing import Any

from tools.semantic_inference import load_taxonomy


OUTCOME_REQUIRED_INTENTS = {
    "definition",
    "feature_selection",
    "driver_analysis",
    "decision_readiness",
}

PROXY_TERMS = {
    "proxy",
    "proxies",
    "candidate",
    "candidates",
    "screen",
    "screening",
    "shortlist",
    "explore",
    "exploratory",
    "hien co",
    "proxy hien co",
    "ung vien",
    "tam thoi",
}

AGGREGATE_OR_RANK_TERMS = {
    "top",
    "bottom",
    "highest",
    "lowest",
    "most",
    "least",
    "rank",
    "ranking",
    "compare",
    "comparison",
    "breakdown",
    "split",
    "by",
    "average",
    "avg",
    "sum",
    "total",
    "revenue",
    "sales",
    "profit",
    "spend",
    "spending",
    "amount",
    "cao nhat",
    "thap nhat",
    "xep hang",
    "so sanh",
    "chia theo",
    "doanh thu",
    "loi nhuan",
}


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _has(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _columns(table_profile: dict[str, Any]) -> set[str]:
    return {str(col.get("name", "")) for col in table_profile.get("columns", [])}


def _proxy_requested(question: str) -> bool:
    normalized = _normalise(question)
    return any(term in normalized for term in PROXY_TERMS)


def _outcome_implied_by_wording(question: str) -> bool:
    normalized = _normalise(question)
    tokens = set(normalized.split())
    terms = load_taxonomy().get("answerability", {}).get("outcome_required_terms", [])
    if not isinstance(terms, list):
        return False
    for term in terms:
        normalized_term = _normalise(term)
        if not normalized_term:
            continue
        if " " in normalized_term:
            if f" {normalized_term} " in f" {normalized} ":
                return True
        elif normalized_term in tokens:
            return True
    return False


def _aggregate_or_rank_question(question: str) -> bool:
    normalized = _normalise(question)
    return any(_normalise(term) and _normalise(term) in normalized for term in AGGREGATE_OR_RANK_TERMS)


def _context_has_confirmed_outcome(context: dict[str, Any], table_profile: dict[str, Any]) -> bool:
    if not bool(context.get("confirmed")):
        return False
    outcome = str(context.get("outcome_column") or "").strip()
    if not outcome or outcome not in _columns(table_profile):
        return False
    return _has(context.get("positive_outcome_value"))


def _context_basics(context: dict[str, Any]) -> bool:
    return _has(context.get("table_purpose")) and _has(context.get("row_grain"))


def evaluate_answerability(
    question: str,
    intent: dict[str, Any],
    table_profile: dict[str, Any],
    semantic_context: dict[str, Any] | None = None,
    semantic_proposal: dict[str, Any] | None = None,
    explicit_aggregate_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = semantic_context or {}
    proposal = semantic_proposal or {}
    proposal_context = proposal.get("context") or {}
    intent_name = intent.get("intent", "analysis")
    outcome_wording = _outcome_implied_by_wording(question)
    requires_outcome = bool(intent.get("requires_validation")) or intent_name in OUTCOME_REQUIRED_INTENTS or outcome_wording
    proxy_requested = _proxy_requested(question)
    confirmed_outcome = _context_has_confirmed_outcome(context, table_profile)
    has_basics = _context_basics(context)
    proposed_outcome = str(proposal_context.get("outcome_column") or "").strip()
    proposed_positive = str(proposal_context.get("positive_outcome_value") or "").strip()

    base = {
        "version": "answerability-v1",
        "intent": intent_name,
        "requires_outcome": requires_outcome,
        "outcome_wording": outcome_wording,
        "proxy_requested": proxy_requested,
        "confirmed_outcome": confirmed_outcome,
        "output_type": "compact_report",
        "status": "answerable",
        "should_stop": False,
        "reason": "The selected table has enough semantic context for this question.",
        "missing_fields": [],
        "warnings": [],
        "suggested_answer": "",
        "explicit_aggregate_plan": explicit_aggregate_plan or {},
    }

    if not table_profile.get("success", True):
        return {
            **base,
            "output_type": "cannot_answer",
            "status": "cannot_answer",
            "should_stop": True,
            "reason": table_profile.get("error") or "The selected table could not be profiled.",
            "suggested_answer": "I cannot analyze this dataset because the table profile is unavailable.",
        }

    if (explicit_aggregate_plan or {}).get("is_explicit"):
        warnings = []
        if outcome_wording and not confirmed_outcome:
            warnings.append(
                "The answer ranks groups by the explicit metric in the question; it does not prove a business outcome."
            )
        return {
            **base,
            "output_type": "metric_dimension_report",
            "status": "answerable_from_schema",
            "should_stop": False,
            "requires_outcome": outcome_wording and not confirmed_outcome,
            "reason": explicit_aggregate_plan.get("reason") or "The question links concrete metrics and dimensions.",
            "warnings": warnings,
            "suggested_answer": "This can be answered directly from the requested metrics and grouping fields.",
        }

    if requires_outcome and confirmed_outcome:
        return base

    if proxy_requested and not confirmed_outcome:
        warnings = [
            "The user explicitly asked for proxy evidence; the output must stay exploratory and must not claim a true outcome.",
        ]
        if proposed_outcome and not proposed_positive:
            warnings.append("A candidate outcome exists, but the positive value is not confirmed.")
        if not proposed_outcome:
            warnings.append("No high-confidence outcome column was detected in the selected table.")
        return {
            **base,
            "output_type": "exploratory_profile",
            "status": "exploratory_only",
            "should_stop": False,
            "reason": "Only proxy/descriptive evidence is allowed until the outcome is confirmed.",
            "requires_outcome": requires_outcome,
            "warnings": warnings,
            "suggested_answer": (
                "I can explore proxy signals in this dataset, but I cannot label any group as truly good, bad, high-risk, "
                "or low-risk until the outcome/business rule is confirmed."
            ),
        }

    if requires_outcome and not confirmed_outcome:
        missing = []
        if not has_basics:
            if not _has(context.get("table_purpose")):
                missing.append("table_purpose")
            if not _has(context.get("row_grain")):
                missing.append("row_grain")
        if not _has(context.get("outcome_column")):
            missing.append("outcome_column")
        elif not _has(context.get("positive_outcome_value")):
            missing.append("positive_outcome_value")

        if not proposed_outcome:
            return {
                **base,
                "output_type": "cannot_answer",
                "status": "cannot_answer",
                "should_stop": True,
                "reason": "This question needs a confirmed outcome, but the selected table does not expose a high-confidence outcome candidate.",
                "missing_fields": missing or ["outcome_column"],
                "warnings": ["Do not generate a decision report from proxy columns alone."],
                "suggested_answer": (
                    "I cannot answer this as a decision/outcome question from the selected table. "
                    "Confirm an outcome column or rephrase the request as proxy/exploratory analysis."
                ),
            }

        return {
            **base,
            "output_type": "semantic_confirmation",
            "status": "needs_context",
            "should_stop": True,
            "reason": "This question needs semantic confirmation before analysis.",
            "missing_fields": missing or ["outcome_column", "positive_outcome_value"],
            "warnings": ["Confirm the suggested semantic context before generating a decision report."],
            "suggested_answer": (
                "I found a possible semantic interpretation, but it needs confirmation before I can answer reliably."
            ),
        }

    if (
        intent_name in {"ranking", "comparison", "segmentation", "trend"}
        and _aggregate_or_rank_question(question)
        and not (explicit_aggregate_plan or {}).get("is_explicit")
        and not _has(context.get("primary_metric"))
    ):
        return {
            **base,
            "output_type": "direct_answer",
            "status": "needs_concrete_metric_or_dimension",
            "should_stop": False,
            "reason": (
                "The question asks for an aggregate/ranking, but the selected table did not link the wording "
                "to a concrete metric plus grouping/date field strongly enough."
            ),
            "warnings": [
                "No aggregate chart/report was generated because the metric/dimension link is not reliable.",
                "Rephrase with the exact metric and grouping column, or confirm the semantic context first.",
            ],
            "suggested_answer": (
                "I cannot safely produce a ranked aggregate from this wording. Please specify the metric and group, "
                "for example: revenue by state, profit by category, or average amount by segment."
            ),
        }

    if intent_name in {"ranking", "comparison", "trend"} and not _has(context.get("primary_metric")):
        return {
            **base,
            "output_type": "direct_answer",
            "status": "answerable_with_warning",
            "reason": "The question can be answered descriptively, but a primary metric would make it more precise.",
            "warnings": ["No confirmed primary metric is set; the system will use the strongest generic measure candidate."],
        }

    return base
