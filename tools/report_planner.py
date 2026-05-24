import re
import unicodedata
from typing import Any

from tools.evidence_planner import build_evidence_plan
from tools.schema_linker import link_schema
from tools.semantic_inference import best_role, load_taxonomy
from tools.viz_planner import build_charts_from_evidence


_LOW_VALUE_ROLES = {"identifier", "text"}
_ANALYTIC_ROLES = {"measure", "dimension", "datetime", "outcome", "binary_flag"}
_PRIMARY_ROLES = {"measure", "dimension", "datetime", "outcome"}


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _terms(text: str) -> set[str]:
    stop = {
        "the", "and", "for", "with", "this", "that", "what", "which", "where", "when",
        "how", "why", "are", "is", "of", "to", "in", "by", "a", "an", "la", "nhung",
        "cac", "cho", "voi", "hay", "khong", "nao", "dau", "co", "kha", "nang",
        "no",
    }
    return {term for term in _normalise(text).split() if len(term) > 1 and term not in stop}


def _fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def _fmt_value(value: Any) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(num) >= 1000:
        return f"{num:,.0f}"
    return f"{num:.3f}".rstrip("0").rstrip(".")


def _column_label(name: str) -> str:
    return str(name).replace("_", " ").strip().title()


def _role_confidence(col: dict[str, Any], role: str | None = None) -> float:
    candidates = col.get("role_candidates", [])
    if role:
        for candidate in candidates:
            if candidate.get("role") == role:
                return float(candidate.get("confidence", 0))
        return 0.0
    return float(candidates[0].get("confidence", 0)) if candidates else 0.0


def _columns_by_role(table_profile: dict[str, Any], role: str, min_confidence: float = 0.35) -> list[dict[str, Any]]:
    cols = []
    for col in table_profile.get("columns", []):
        confidence = _role_confidence(col, role)
        if confidence >= min_confidence:
            cols.append({**col, "_role_confidence": confidence})
    cols.sort(key=lambda item: item.get("_role_confidence", 0), reverse=True)
    return cols


def _name_relevance(col_name: str, question_terms: set[str]) -> float:
    if not question_terms:
        return 0.0
    name_terms = _terms(col_name)
    if not name_terms:
        return 0.0
    overlap = name_terms & question_terms
    partial = {
        qt for qt in question_terms
        if len(qt) >= 3 and any((qt in nt or nt in qt) and len(nt) >= 3 for nt in name_terms)
    }
    return min(1.0, (len(overlap) * 0.6 + len(partial) * 0.25) / max(1, len(question_terms)))


def _concept_relevance(col_name: str, question: str) -> float:
    """Config-driven bridge between user wording and generic column names."""
    taxonomy = load_taxonomy()
    normalized_question = _normalise(question)
    normalized_col = _normalise(col_name)
    if not normalized_question or not normalized_col:
        return 0.0

    best = 0.0
    for concept in taxonomy.get("concepts", {}).values():
        question_hits = [
            phrase for phrase in concept.get("question_terms", [])
            if _normalise(phrase) and _normalise(phrase) in normalized_question
        ]
        if not question_hits:
            continue

        strong_hits = [
            term for term in concept.get("strong_column_terms", [])
            if _normalise(term) and _normalise(term) in normalized_col
        ]
        supporting_hits = [
            term for term in concept.get("supporting_column_terms", [])
            if _normalise(term) and _normalise(term) in normalized_col
        ]
        low_priority_hits = [
            term for term in concept.get("low_priority_column_terms", [])
            if _normalise(term) and _normalise(term) in normalized_col
        ]
        legacy_hits = [
            term for term in concept.get("column_terms", [])
            if _normalise(term) and _normalise(term) in normalized_col
        ]
        if strong_hits:
            best = max(best, min(1.0, 0.65 + 0.1 * len(strong_hits) + 0.05 * len(supporting_hits)))
        elif supporting_hits:
            support_score = min(0.55, 0.32 + 0.08 * len(supporting_hits))
            if low_priority_hits:
                support_score = min(support_score, 0.25)
            best = max(best, support_score)
        elif legacy_hits:
            best = max(best, min(1.0, 0.35 + 0.2 * len(legacy_hits)))
    return best


def _technical_penalty(col: dict[str, Any], relevance: float, concept_relevance: float) -> float:
    role = best_role(col)
    name = _normalise(col.get("name", ""))
    penalty = 0.0
    if role in _LOW_VALUE_ROLES:
        penalty += 0.35
    if float(col.get("missing_pct", 0) or 0) >= 80:
        penalty += 0.2
    if role == "binary_flag" and max(relevance, concept_relevance) < 0.35:
        penalty += 0.35
    if any(token in name.split() for token in {"flag", "id", "uuid"}):
        penalty += 0.12
    return penalty


def _is_usable_column(col: dict[str, Any]) -> bool:
    role = best_role(col)
    if role in _LOW_VALUE_ROLES:
        return False
    if col.get("missing_pct", 0) >= 95:
        return False
    if col.get("sample_distinct_count", 0) <= 1:
        return False
    return True


def _rank_columns_for_question(question: str, table_profile: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
    question_terms = _terms(question)
    ranked = []
    for col in table_profile.get("columns", []):
        role = best_role(col)
        role_score = {
            "outcome": 0.5,
            "measure": 0.45,
            "dimension": 0.32,
            "datetime": 0.28,
            "binary_flag": 0.2,
        }.get(role, 0.08)
        confidence = _role_confidence(col)
        relevance = _name_relevance(col.get("name", ""), question_terms)
        concept_relevance = _concept_relevance(col.get("name", ""), question)
        completeness = max(0.0, 1.0 - float(col.get("missing_pct", 0)) / 100)
        cardinality = col.get("sample_distinct_count", 0)
        cardinality_score = 0.2 if 1 < cardinality <= 50 else 0.1 if cardinality > 50 else 0.0
        penalty = _technical_penalty(col, relevance, concept_relevance)

        raw_score = (
            role_score
            + confidence * 0.25
            + relevance * 0.25
            + concept_relevance * 0.45
            + completeness * 0.18
            + cardinality_score
            - penalty
        )
        score = round(max(0.0, min(1.0, raw_score)), 3)
        if _is_usable_column(col) or relevance > 0 or concept_relevance > 0:
            ranked.append({
                "Column": col.get("name", ""),
                "Role": role.replace("_", " ").title(),
                "Score": f"{max(score, 0):.2f}",
                "Missing": _fmt_pct(col.get("missing_pct", 0)),
                "Sample Distinct": _fmt_int(col.get("sample_distinct_count", 0)),
                "Why It May Matter": _reason_for_column(role, relevance, concept_relevance, col),
                "_score": score,
            })

    ranked.sort(key=lambda item: item["_score"], reverse=True)
    return [{k: v for k, v in item.items() if k != "_score"} for item in ranked[:limit]]


def _reason_for_column(role: str, relevance: float, concept_relevance: float, col: dict[str, Any]) -> str:
    parts = []
    if relevance > 0:
        parts.append("name overlaps with the question")
    if concept_relevance > 0:
        parts.append("matches a configured semantic concept for the question")
    if role == "measure":
        parts.append("numeric measure usable for scoring, ranking, or comparison")
    elif role == "dimension":
        parts.append("grouping field usable for segmentation")
    elif role == "datetime":
        parts.append("time field usable for trend analysis")
    elif role == "outcome":
        parts.append("possible validated target if confirmed")
    elif role == "binary_flag":
        parts.append("binary feature or status signal, not automatically a target")
    else:
        parts.append("available attribute with limited inferred analytic role")
    if col.get("missing_pct", 0) > 40:
        parts.append("use cautiously because missingness is high")
    return "; ".join(parts)


def _data_scope(table_profile: dict[str, Any], raw_row_count: int) -> dict[str, Any]:
    return {
        "source": "full_table_profile" if table_profile.get("table_name") else "sample_profile",
        "total_rows": table_profile.get("row_count"),
        "sample_rows": table_profile.get("sample_row_count", raw_row_count),
        "profiled_columns": table_profile.get("columns_profiled", 0),
        "deep_profiled_columns": table_profile.get("columns_deep_profiled", table_profile.get("columns_profiled", 0)),
        "total_columns": table_profile.get("columns_total", 0),
    }


def _true_outcome_cols(table_profile: dict[str, Any], semantic_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    context = semantic_context or {}
    confirmed = str(context.get("outcome_column") or "").strip()
    if confirmed:
        matches = [
            {**col, "_role_confidence": 1.0, "_confirmed_outcome": True}
            for col in table_profile.get("columns", [])
            if col.get("name") == confirmed
        ]
        if matches:
            return matches
    return _columns_by_role(table_profile, "outcome", min_confidence=0.7)


def _evidence_level(
    intent: dict[str, Any],
    outcome_cols: list[dict[str, Any]],
    table_profile: dict[str, Any],
    semantic_context: dict[str, Any] | None = None,
) -> tuple[str, str]:
    if not table_profile.get("success", True):
        return "limited", "low"
    if outcome_cols and (semantic_context or {}).get("outcome_column"):
        return "validated_context", "medium"
    if intent.get("requires_validation") and not outcome_cols:
        return "proxy_based", "low"
    if outcome_cols:
        return "outcome_available", "medium"
    return "descriptive", "medium"


def _summary_cards(table_profile: dict[str, Any], evidence_level: str, decision_readiness: str) -> list[dict[str, str]]:
    role_counts = table_profile.get("role_counts", {})
    return [
        {"label": "Rows", "value": _fmt_int(table_profile.get("row_count")), "note": "Total rows in profiled scope"},
        {"label": "Evidence", "value": evidence_level.replace("_", " ").title(), "note": "Strength of available support"},
        {"label": "Readiness", "value": decision_readiness.title(), "note": "Use with stated limitations"},
        {"label": "Candidate Signals", "value": _fmt_int(role_counts.get("measure", 0) + role_counts.get("dimension", 0)), "note": "Usable measures and grouping fields"},
    ]


def _warnings(
    intent: dict[str, Any],
    table_profile: dict[str, Any],
    outcome_cols: list[dict[str, Any]],
    semantic_context: dict[str, Any] | None = None,
) -> list[str]:
    warnings = list(table_profile.get("warnings", []))
    if table_profile.get("sample_row_count") != table_profile.get("row_count"):
        warnings.append("Displayed rows are a bounded sample; table-level statistics come from the profile where available.")
    if intent.get("requires_validation") and not outcome_cols:
        warnings.append("No confirmed outcome column was detected, so explanatory or decision-oriented answers are candidate/proxy-based.")
    if outcome_cols and not (semantic_context or {}).get("positive_outcome_value"):
        warnings.append("An outcome column is available, but its positive/desired value has not been confirmed.")
    if table_profile.get("columns_deep_profiled", 0) < table_profile.get("columns_total", 0):
        warnings.append("Some columns only received shallow profiling; deep numeric/date statistics were capped for runtime safety.")
    return list(dict.fromkeys(warnings))


def _charts(table_profile: dict[str, Any], dimension_cols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    charts = []
    missing = [
        {"label": _column_label(col["name"]), "value": col.get("missing_pct", 0)}
        for col in table_profile.get("columns", [])
        if col.get("missing_pct", 0) > 0
    ]
    missing = sorted(missing, key=lambda item: item["value"], reverse=True)[:10]
    if missing:
        charts.append({
            "id": "missingness",
            "type": "bar",
            "title": "Missing Data by Column",
            "subtitle": "Percent of rows without a value",
            "data": missing,
            "unit": "%",
        })

    for col in dimension_cols:
        values = col.get("sample_top_values", [])
        if len(values) >= 2:
            charts.append({
                "id": f"distribution_{col['name']}",
                "type": "bar",
                "title": f"Sample Distribution - {_column_label(col['name'])}",
                "subtitle": "Top observed values in the bounded sample",
                "data": values[:10],
                "unit": "rows",
            })
            break
    return charts


def _candidate_score_chart(ranked_cols: list[dict[str, Any]]) -> dict[str, Any] | None:
    data = []
    for row in ranked_cols[:5]:
        try:
            score = float(row.get("Score", 0))
        except (TypeError, ValueError):
            score = 0.0
        data.append({"label": row.get("Column", ""), "value": round(score * 100, 1)})
    if not data:
        return None
    return {
        "id": "candidate_signal_score",
        "type": "bar",
        "title": "Top Candidate Signals",
        "subtitle": "Generic relevance and usability score; not model feature importance",
        "data": data,
        "unit": "score",
    }


def _candidate_missingness_chart(ranked_cols: list[dict[str, Any]]) -> dict[str, Any] | None:
    data = []
    for row in ranked_cols[:5]:
        try:
            missing = float(str(row.get("Missing", "0")).replace("%", ""))
        except (TypeError, ValueError):
            missing = 0.0
        data.append({"label": row.get("Column", ""), "value": round(missing, 1)})
    if not data or all(item["value"] == 0 for item in data):
        return None
    return {
        "id": "candidate_missingness",
        "type": "bar",
        "title": "Candidate Data Quality",
        "subtitle": "Missing percent among the shortlisted columns",
        "data": data,
        "unit": "%",
    }


def _distribution_chart_from_slicers(slicers: list[dict[str, Any]]) -> dict[str, Any] | None:
    for slicer in slicers:
        values = slicer.get("top_values", [])
        if slicer.get("type") == "category" and len(values) >= 2:
            return {
                "id": f"mix_{slicer['column']}",
                "type": "donut",
                "title": f"Sample Mix - {_column_label(slicer['column'])}",
                "subtitle": "Top observed values in embedded sample",
                "dimension": slicer["column"],
                "metric": "__count__",
                "aggregation": "count",
            }
    return None


def _smart_slicers(table_profile: dict[str, Any], question: str, limit: int = 2) -> list[dict[str, Any]]:
    question_terms = _terms(question)
    candidates = []
    for col in table_profile.get("columns", []):
        role = best_role(col)
        name = col.get("name", "")
        missing = float(col.get("missing_pct", 0) or 0)
        distinct = int(col.get("sample_distinct_count", 0) or 0)
        relevance = _name_relevance(name, question_terms)
        concept_relevance = _concept_relevance(name, question)
        if role in {"identifier", "text", "outcome"} or missing >= 90 or distinct <= 1:
            continue

        slicer_type = None
        if role == "dimension" and 2 <= distinct <= 30:
            slicer_type = "category"
        elif role == "binary_flag" and 2 <= distinct <= 3 and max(relevance, concept_relevance) >= 0.45:
            slicer_type = "category"
        elif role == "measure" and distinct > 2:
            slicer_type = "range"
        elif role == "datetime":
            slicer_type = "date"
        if not slicer_type:
            continue

        score = (
            _role_confidence(col) * 0.35
            + relevance * 0.25
            + concept_relevance * 0.35
            + (1 - missing / 100) * 0.2
            + (0.1 if slicer_type == "category" and distinct <= 15 else 0)
            - _technical_penalty(col, relevance, concept_relevance) * 0.5
        )
        slicer = {
            "column": name,
            "label": _column_label(name),
            "type": slicer_type,
            "role": role,
            "missing_pct": missing,
            "distinct_count": distinct,
            "top_values": col.get("sample_top_values", [])[:20],
        }
        if slicer_type in {"range", "date"}:
            slicer["min"] = col.get("min")
            slicer["max"] = col.get("max")
        candidates.append((score, slicer))

    candidates.sort(key=lambda item: item[0], reverse=True)
    selected: list[dict[str, Any]] = []
    used_types: set[str] = set()
    for _, slicer in candidates:
        if len(selected) >= limit:
            break
        if slicer["type"] in used_types and len(candidates) > limit:
            continue
        selected.append(slicer)
        used_types.add(slicer["type"])
    for _, slicer in candidates:
        if len(selected) >= limit:
            break
        if slicer not in selected:
            selected.append(slicer)
    return selected[:limit]


def _dashboard_charts(
    table_profile: dict[str, Any],
    question: str,
    ranked_cols: list[dict[str, Any]],
    slicers: list[dict[str, Any]],
    limit: int = 2,
) -> list[dict[str, Any]]:
    charts: list[dict[str, Any]] = []
    score_chart = _candidate_score_chart(ranked_cols)
    missing_chart = _candidate_missingness_chart(ranked_cols)
    distribution_chart = _distribution_chart_from_slicers(slicers)

    if missing_chart:
        charts.append(missing_chart)
    elif score_chart:
        charts.append(score_chart)

    if distribution_chart:
        charts.append(distribution_chart)
    elif score_chart and score_chart not in charts:
        charts.append(score_chart)

    return charts[:limit]


def _quality_rows(table_profile: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for col in sorted(table_profile.get("columns", []), key=lambda item: item.get("missing_pct", 0), reverse=True)[:12]:
        rows.append({
            "Column": col["name"],
            "Role": best_role(col).replace("_", " ").title(),
            "Type": col.get("data_type", ""),
            "Missing": _fmt_pct(col.get("missing_pct", 0)),
            "Sample Distinct": _fmt_int(col.get("sample_distinct_count", 0)),
        })
    return rows


def _direct_answer(question: str, intent: dict[str, Any], outcome_cols: list[dict[str, Any]], ranked_cols: list[dict[str, Any]]) -> str:
    intent_name = intent.get("intent", "analysis").replace("_", " ")
    if outcome_cols and any(col.get("_confirmed_outcome") for col in outcome_cols):
        names = ", ".join(row["Column"] for row in ranked_cols[:5]) if ranked_cols else "the confirmed semantic fields"
        return (
            f"This is a {intent_name} question. A confirmed outcome column is available, "
            f"so the report can be grounded in the semantic layer. Start with: {names}."
        )
    if intent.get("requires_validation") and not outcome_cols:
        return (
            f"This is a {intent_name} question. The table can shortlist candidate signals, "
            "but it cannot prove the requested business outcome until a confirmed outcome/target column or business rule is available."
        )
    if ranked_cols:
        names = ", ".join(row["Column"] for row in ranked_cols[:5])
        return (
            f"This is a {intent_name} question. Based on generic column roles, data quality, and question relevance, "
            f"the strongest candidate columns to inspect first are: {names}."
        )
    return (
        f"This is a {intent_name} question, but the profiled table does not expose enough usable columns "
        "to form a strong answer without additional context."
    )


def _candidate_cards(ranked_cols: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    cards = []
    for idx, row in enumerate(ranked_cols[:limit], start=1):
        cards.append({
            "rank": idx,
            "column": row.get("Column", ""),
            "role": row.get("Role", ""),
            "score": row.get("Score", ""),
            "missing": row.get("Missing", ""),
            "reason": row.get("Why It May Matter", ""),
        })
    return cards


def _linked_to_ranked_rows(linked_columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in linked_columns:
        rows.append({
            "Column": item.get("column", ""),
            "Role": str(item.get("role", "")).replace("_", " ").title(),
            "Score": f"{float(item.get('score', 0) or 0):.2f}",
            "Missing": _fmt_pct(item.get("missing_pct", 0)),
            "Sample Distinct": _fmt_int(item.get("sample_distinct_count", 0)),
            "Why It May Matter": item.get("reason", ""),
        })
    return rows


def _static_evidence_results(evidence_plan: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for item in evidence_plan.get("items", []):
        if item.get("sql"):
            continue
        results.append({
            "id": item.get("id"),
            "kind": item.get("kind"),
            "title": item.get("title"),
            "description": item.get("description"),
            "scope": item.get("scope", "profile"),
            "success": True,
            "data": item.get("data", []),
            "sql": "",
        })
    return results


def _data_context_summary(data_context: dict[str, Any] | None) -> dict[str, Any]:
    if not data_context:
        return {}
    tables = []
    for table in data_context.get("tables", []):
        tables.append({
            "table_name": table.get("table_name"),
            "row_count": table.get("row_count"),
            "sample_row_count": table.get("sample_row_count"),
            "columns_total": table.get("columns_total"),
        })
    return {
        "version": data_context.get("version"),
        "mode": data_context.get("mode"),
        "constraints": data_context.get("constraints", {}),
        "tables": tables,
    }


def _direct_answer_with_evidence(
    question: str,
    intent: dict[str, Any],
    outcome_cols: list[dict[str, Any]],
    ranked_cols: list[dict[str, Any]],
    evidence_results: list[dict[str, Any]],
) -> str:
    for item in evidence_results:
        if not item.get("success", True):
            continue
        kind = item.get("kind")
        data = item.get("data") or []
        if kind in {"outcome_rate_by_dimension", "metric_by_dimension", "distribution"} and data:
            values = []
            for row in data[:3]:
                label = row.get("label")
                value = row.get("value")
                if label is not None and value is not None:
                    values.append(f"{label} ({_fmt_value(value)})")
            if not values:
                continue
            qualifier = ""
            if not outcome_cols:
                qualifier = " This is proxy/descriptive evidence, not proof of the true business outcome."
            return (
                f"For this {intent.get('intent', 'analysis').replace('_', ' ')} question, "
                f"the strongest evidence available is `{item.get('title')}`. "
                f"Top observed groups/values are: {', '.join(values)}.{qualifier}"
            )
    return _direct_answer(question, intent, outcome_cols, ranked_cols)


def _executive_points(
    intent: dict[str, Any],
    outcome_cols: list[dict[str, Any]],
    ranked_cols: list[dict[str, Any]],
    warnings: list[str],
) -> list[str]:
    points = []
    if outcome_cols:
        points.append("A confirmed or candidate outcome exists, so analysis can be validated against that field.")
    else:
        points.append("No confirmed outcome is available; any answer must stay at proxy or screening level.")

    if ranked_cols:
        names = ", ".join(row["Column"] for row in ranked_cols[:5])
        points.append(f"First 5 columns to inspect: {names}.")
    else:
        points.append("There are not enough usable columns to create a trustworthy shortlist.")

    points.append(
        "Scores combine generic role inference, missingness, sample distinctness, and question relevance; they are not model feature importance."
    )

    high_missing = []
    for row in ranked_cols[:5]:
        try:
            missing = float(str(row.get("Missing", "0")).replace("%", ""))
        except (TypeError, ValueError):
            missing = 0.0
        if missing >= 40:
            high_missing.append(row["Column"])
    if high_missing:
        points.append(f"Use caution with missing data among candidates, especially: {', '.join(high_missing[:3])}.")
    elif warnings:
        points.append(warnings[0])
    else:
        points.append("No major data warning was detected among the main candidate fields.")

    if intent.get("requires_validation") and not outcome_cols:
        points.append(
            "Next step: confirm the outcome or business rule before measuring feature importance or building a score."
        )
    else:
        points.append("Next step: validate against the confirmed outcome to separate signal from noise.")
    return points[:5]


def _intent_guidance(intent: dict[str, Any]) -> str:
    name = intent.get("intent", "analysis")
    guidance = {
        "definition": "Use this report to identify candidate criteria, then validate the criteria against a confirmed outcome or business rule.",
        "feature_selection": "Use this report to shortlist columns for follow-up modeling or validation; do not treat rankings as feature importance.",
        "driver_analysis": "Use this report to identify possible explanatory fields; causal or predictive claims require validated outcomes.",
        "ranking": "Use measure and dimension candidates to build explicit top/bottom queries.",
        "segmentation": "Use dimension candidates and distributions to decide useful grouping fields.",
        "trend": "Confirm datetime fields before trusting trend output.",
        "comparison": "Confirm the comparison groups and primary metric before operational use.",
        "anomaly": "Use missingness, cardinality, and extreme values as anomaly starting points.",
        "data_quality": "Prioritize missingness, low-cardinality surprises, and columns with weak usability.",
        "decision_readiness": "Treat the report as decision support only when the evidence level is validated or outcome-available.",
    }
    return guidance.get(name, "Use the profile to decide the next concrete analysis query.")


def build_report_spec(
    question: str,
    intent: dict[str, Any],
    table_profile: dict[str, Any],
    raw_row_count: int = 0,
    semantic_context: dict[str, Any] | None = None,
    mschema: dict[str, Any] | None = None,
    data_context: dict[str, Any] | None = None,
    linked_columns: list[dict[str, Any]] | None = None,
    evidence_plan: dict[str, Any] | None = None,
    evidence_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    outcome_cols = _true_outcome_cols(table_profile, semantic_context)
    measure_cols = _columns_by_role(table_profile, "measure")
    dimension_cols = _columns_by_role(table_profile, "dimension")
    linked_columns = linked_columns or link_schema(question, table_profile, semantic_context, limit=12)
    ranked_cols = _linked_to_ranked_rows(linked_columns) or _rank_columns_for_question(question, table_profile)
    evidence_level, decision_readiness = _evidence_level(intent, outcome_cols, table_profile, semantic_context)
    warnings = _warnings(intent, table_profile, outcome_cols, semantic_context)
    evidence_plan = evidence_plan or build_evidence_plan(
        question=question,
        intent=intent,
        table_profile=table_profile,
        semantic_context=semantic_context,
        linked_columns=linked_columns,
    )
    evidence_results = evidence_results if evidence_results is not None else _static_evidence_results(evidence_plan)
    candidate_cards = _candidate_cards(ranked_cols)
    executive_points = _executive_points(intent, outcome_cols, ranked_cols, warnings)
    slicers: list[dict[str, Any]] = []
    charts = build_charts_from_evidence(evidence_results)
    if not charts:
        charts = _dashboard_charts(table_profile, question, ranked_cols, slicers)

    sections = [
        {
            "title": "Direct Answer",
            "kind": "paragraph",
            "content": _direct_answer_with_evidence(question, intent, outcome_cols, ranked_cols, evidence_results),
        },
        {
            "title": "Question-Aware Candidate Columns",
            "kind": "bullets",
            "items": [
                f"{card['rank']}. {card['column']} ({card['role']}, score {card['score']}) - {card['reason']}"
                for card in candidate_cards
            ],
        },
        {
            "title": "How To Use This Answer",
            "kind": "bullets",
            "items": [
                _intent_guidance(intent),
                "Column rankings are generic screening signals, not proof of predictive importance.",
                "Identifiers, constant fields, and highly missing fields are down-ranked automatically.",
            ],
        },
        {
            "title": "Data Quality Snapshot",
            "kind": "bullets",
            "items": [
                f"{row['Column']}: {row['Missing']} missing, role {row['Role']}, sample distinct {row['Sample Distinct']}"
                for row in _quality_rows(table_profile)[:5]
            ],
        },
        {
            "title": "Limitations",
            "kind": "bullets",
            "items": warnings or ["No major profiling limitations were detected."],
        },
    ]

    return {
        "version": "generic-report-spec-v4",
        "question": question,
        "intent": intent,
        "data_scope": _data_scope(table_profile, raw_row_count),
        "data_context": _data_context_summary(data_context),
        "evidence_level": evidence_level,
        "decision_readiness": decision_readiness,
        "warnings": warnings,
        "semantic_context": semantic_context or {},
        "mschema": mschema or {},
        "linked_columns": linked_columns,
        "evidence_plan": evidence_plan,
        "evidence_results": evidence_results,
        "summary_cards": _summary_cards(table_profile, evidence_level, decision_readiness),
        "executive_points": executive_points,
        "candidate_signals": candidate_cards,
        "slicers": slicers,
        "charts": charts,
        "sections": sections,
        "recommended_next_steps": [
            "If the question implies prediction or decision-making, identify or add the true outcome column first.",
            "Use the candidate column list to run a focused follow-up query or validation test.",
            "Review high-missing columns before relying on them in scoring, segmentation, or explanation.",
        ],
    }


def analytics_from_report_spec(report_spec: dict[str, Any]) -> dict[str, Any]:
    cards = report_spec.get("summary_cards", [])
    warnings = report_spec.get("warnings", [])
    sections = report_spec.get("sections", [])
    direct = ""
    insights = []
    for section in sections:
        if section.get("title") == "Direct Answer":
            direct = section.get("content", "")
        elif section.get("kind") == "paragraph" and section.get("content"):
            insights.append(section["content"])

    executive_points = report_spec.get("executive_points", [])
    candidate_signals = report_spec.get("candidate_signals", [])
    return {
        "summary": direct or "A generic question-aware report was generated for the selected data.",
        "kpis": [
            {"name": card.get("label", ""), "value": card.get("value", ""), "interpretation": card.get("note", "")}
            for card in cards[:6]
        ],
        "insights": executive_points or insights,
        "anomalies": warnings,
        "recommendation": "Finding -> So What -> Now What: use the direct answer and candidate columns as a generic shortlist, then validate against the specific metric or outcome the question requires.",
        "hypotheses": [
            {
                "name": item.get("column", ""),
                "description": item.get("reason", ""),
                "confidence": item.get("score", ""),
            }
            for item in candidate_signals[:5]
        ],
    }
