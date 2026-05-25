from typing import Any


def _chart_data(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    data = []
    for row in rows:
        label = row.get("label") if row.get("label") is not None else row.get("period")
        value = row.get("value")
        if label is None or value is None:
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        data.append({"label": str(label), "value": round(numeric_value, 4)})
    return data


def _series_chart_data(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    data = []
    for row in rows:
        label = row.get("label")
        series = row.get("series")
        value = row.get("value")
        if label is None or series is None or value is None:
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        data.append({
            "label": str(label),
            "series": str(series),
            "value": round(numeric_value, 4),
        })
    return data


def _select_chart_type(kind: str, data: list[dict[str, Any]]) -> str:
    """
    Chart type is driven entirely by what the evidence kind is and the data shape.
    No hardcoded question-phrase matching.
    """
    n = len(data)
    if kind == "trend":
        return "line"
    if kind == "metric_by_two_dimensions":
        return "stacked_bar"
    if kind in ("outcome_rate_by_dimension", "distribution"):
        return "doughnut" if 2 <= n <= 6 else "bar"
    if kind in ("metric_by_dimension", "top_n"):
        return "bar"
    if kind == "candidate_scores":
        return "bar"
    # fallback
    return "bar"


def build_charts_from_evidence(
    evidence_results: list[dict[str, Any]],
    max_charts: int = 2,
) -> list[dict[str, Any]]:
    charts: list[dict[str, Any]] = []

    # candidate_scores = schema-linking metadata, not business data.
    # Only use it as a last resort when no real evidence produced a chart.
    NEVER_CHART = {"missingness", "numeric_summary"}
    LAST_RESORT = {"candidate_scores"}

    priority = {
        "outcome_rate_by_dimension": 0,
        "metric_by_two_dimensions": 1,
        "trend": 2,
        "top_n": 3,
        "metric_by_dimension": 4,
        "distribution": 5,
    }

    def _make_chart(item: dict[str, Any]) -> dict[str, Any] | None:
        if not item.get("success", True):
            return None
        kind = item.get("kind", "")
        data = _series_chart_data(item.get("data", [])) if kind == "metric_by_two_dimensions" else _chart_data(item.get("data", []))
        if len(data) < 2:
            return None
        chart_type = _select_chart_type(kind, data)
        return {
            "id": item.get("id"),
            "type": chart_type,
            "title": item.get("title", ""),
            "subtitle": item.get("description", ""),
            "data": data[:24] if chart_type == "line" else data[:12],
            "unit": item.get("unit", ""),
            "x_label": item.get("x_label", ""),
            "y_label": item.get("y_label", ""),
            "source_evidence_id": item.get("id"),
            "metric": item.get("metric", ""),
            "primary_dimension": item.get("primary_dimension", ""),
            "series_dimension": item.get("series_dimension", ""),
        }

    # First pass: real business evidence only
    real_evidence = [
        item for item in evidence_results
        if item.get("kind", "") not in NEVER_CHART | LAST_RESORT
    ]
    ordered = sorted(real_evidence, key=lambda i: priority.get(i.get("kind", ""), 50))
    for item in ordered:
        if len(charts) >= max_charts:
            break
        chart = _make_chart(item)
        if chart:
            charts.append(chart)

    # Second pass: candidate_scores only if no real chart was produced
    if not charts:
        for item in evidence_results:
            if item.get("kind") in LAST_RESORT:
                chart = _make_chart(item)
                if chart:
                    charts.append(chart)
                    break

    return charts
