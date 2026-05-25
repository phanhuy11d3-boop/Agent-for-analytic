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


def _column_names(table_profile: dict[str, Any]) -> set[str]:
    return {str(col.get("name", "")) for col in table_profile.get("columns", [])}


def _has_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


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


def _gap(gap_id: str, question: str, reason: str, required: bool = True, field: str | None = None) -> dict[str, Any]:
    return {
        "id": gap_id,
        "question": question,
        "reason": reason,
        "required": required,
        "field": field or gap_id,
    }


def detect_semantic_gaps(
    question: str,
    intent: dict[str, Any],
    table_profile: dict[str, Any],
    semantic_context: dict[str, Any],
) -> dict[str, Any]:
    context = semantic_context or {}
    columns = _column_names(table_profile)
    intent_name = intent.get("intent", "analysis")
    gaps: list[dict[str, Any]] = []
    requires_outcome = (
        bool(intent.get("requires_validation"))
        or intent_name in OUTCOME_REQUIRED_INTENTS
        or _outcome_implied_by_wording(question)
    )
    requires_business_context = requires_outcome or intent_name in {
        "ranking",
        "comparison",
        "trend",
        "segmentation",
        "driver_analysis",
        "decision_readiness",
    }

    if not _has_value(context.get("table_purpose")):
        gaps.append(_gap(
            "table_purpose",
            "What business process or decision does this table support?",
            "Business purpose helps the report decide which signals matter most.",
            required=requires_outcome,  # only block for decision/driver/feature questions
            field="table_purpose",
        ))

    if not _has_value(context.get("row_grain")):
        gaps.append(_gap(
            "row_grain",
            "What does one row represent?",
            "Row grain prevents the report from mixing entity-level evidence.",
            required=requires_outcome,  # only block for decision/driver/feature questions
            field="row_grain",
        ))

    outcome_column = str(context.get("outcome_column") or "").strip()
    if requires_business_context and not bool(context.get("confirmed")):
        gaps.append(_gap(
            "confirm_semantic_context",
            "Please confirm or correct the suggested semantic context for this table.",
            "Inferred table meaning is only a draft until a user confirms it.",
            required=requires_outcome,
            field="confirmed",
        ))

    if requires_outcome:
        if not outcome_column:
            gaps.append(_gap(
                "outcome_column",
                "Which column is the confirmed outcome, label, status, or business rule for this question?",
                "Decision, definition, driver, and feature-selection questions need a confirmed outcome or business rule.",
                field="outcome_column",
            ))
        elif outcome_column not in columns:
            gaps.append(_gap(
                "outcome_column_missing",
                f"The stored outcome column `{outcome_column}` does not exist in the current schema. Which column should be used?",
                "Stored semantic context no longer matches the uploaded table.",
                field="outcome_column",
            ))
        elif not _has_value(context.get("positive_outcome_value")):
            gaps.append(_gap(
                "positive_outcome_value",
                f"In `{outcome_column}`, which value means the desired or positive outcome?",
                "Binary/status outcomes are ambiguous unless the positive value is confirmed.",
                field="positive_outcome_value",
            ))

    if intent_name in {"ranking", "comparison", "trend", "segmentation"} and not _has_value(context.get("primary_metric")):
        gaps.append(_gap(
            "primary_metric",
            "What is the primary metric to optimize, compare, or track?",
            "Ranking, comparison, segmentation, and trend reports need a primary metric to avoid generic charts.",
            required=False,
            field="primary_metric",
        ))

    blocking = [gap for gap in gaps if gap.get("required")]
    return {
        "clarification_required": bool(blocking),
        "gaps": gaps,
        "blocking_gap_count": len(blocking),
        "question": question,
        "intent": intent_name,
    }


def build_context_required_spec(
    question: str,
    intent: dict[str, Any],
    table_profile: dict[str, Any],
    semantic_context: dict[str, Any],
    mschema: dict[str, Any],
    gaps: list[dict[str, Any]],
    semantic_proposal: dict[str, Any] | None = None,
    answerability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    required = [gap for gap in gaps if gap.get("required")]
    optional = [gap for gap in gaps if not gap.get("required")]
    answerability = answerability or {}
    semantic_proposal = semantic_proposal or {}
    proposal_context = semantic_proposal.get("context") or {}
    mode = answerability.get("output_type") or "context_required"
    direct_answer = answerability.get("suggested_answer") or (
        "There is not enough confirmed semantic context to answer reliably. Confirm the business meaning first, then generate the report."
    )
    proposal_items = []
    for label, field in (
        ("Table purpose", "table_purpose"),
        ("Row grain", "row_grain"),
        ("Outcome column", "outcome_column"),
        ("Positive outcome value", "positive_outcome_value"),
        ("Primary metric", "primary_metric"),
    ):
        value = proposal_context.get(field)
        if value:
            score = (semantic_proposal.get("confidence") or {}).get(field)
            suffix = f" (confidence {score})" if score is not None else ""
            proposal_items.append(f"{label}: {value}{suffix}")

    return {
        "version": "generic-report-spec-v3",
        "mode": mode,
        "output_type": mode,
        "question": question,
        "intent": intent,
        "data_scope": {
            "source": "semantic_gate",
            "total_rows": table_profile.get("row_count"),
            "profiled_columns": table_profile.get("columns_profiled", 0),
            "total_columns": table_profile.get("columns_total", 0),
        },
        "evidence_level": "insufficient_semantic_context",
        "decision_readiness": "blocked",
        "semantic_context": semantic_context,
        "semantic_proposal": semantic_proposal,
        "answerability": answerability,
        "mschema": mschema,
        "semantic_gaps": gaps,
        "warnings": list(dict.fromkeys([
            "Report generation was blocked because the business meaning of the table is not confirmed.",
            "No business conclusion should be made until the required semantic questions are answered.",
            *(answerability.get("warnings") or []),
            *(semantic_proposal.get("warnings") or []),
        ])),
        "summary_cards": [
            {"label": "Rows", "value": f"{int(table_profile.get('row_count') or 0):,}", "note": "Rows in selected table"},
            {"label": "Columns", "value": f"{int(table_profile.get('columns_total') or 0):,}", "note": "Columns in selected table"},
            {"label": "Required Context", "value": str(len(required)), "note": "Questions blocking trustworthy analysis"},
            {"label": "Optional Context", "value": str(len(optional)), "note": "Questions that improve report quality"},
        ],
        "executive_points": [
            "Do not read a decision report for this question until semantic context is confirmed.",
            "The system needs business purpose, row grain, and outcome/business rule before making conclusions.",
            "Schema names and sample values can produce an M-Schema draft, but they do not prove business meaning.",
            "After confirmation, the semantic layer will be reused for future questions on the same table schema.",
            "This is an intentional fail-safe to avoid a long report that looks polished but answers the wrong question.",
        ],
        "candidate_signals": [],
        "slicers": [],
        "charts": [],
        "sections": [
            {
                "title": "Direct Answer",
                "kind": "paragraph",
                "content": direct_answer,
            },
            {
                "title": "Suggested Semantic Context",
                "kind": "bullets",
                "items": proposal_items or ["No safe semantic proposal was produced from the table profile."],
            },
            {
                "title": "Required Questions",
                "kind": "bullets",
                "items": [gap["question"] for gap in required],
            },
            {
                "title": "Optional Questions",
                "kind": "bullets",
                "items": [gap["question"] for gap in optional] or ["No optional questions."],
            },
        ],
        "recommended_next_steps": [gap["question"] for gap in gaps[:5]],
    }
