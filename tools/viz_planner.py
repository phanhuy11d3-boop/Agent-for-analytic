from typing import Any


def _chart_data(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    data = []
    for row in rows:
        label = row.get("label")
        value = row.get("value")
        if label is None or value is None:
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        data.append({"label": str(label), "value": round(numeric_value, 3)})
    return data


def build_charts_from_evidence(
    evidence_results: list[dict[str, Any]],
    max_charts: int = 2,
) -> list[dict[str, Any]]:
    charts: list[dict[str, Any]] = []
    preferred_order = {
        "outcome_rate_by_dimension": 0,
        "metric_by_dimension": 1,
        "distribution": 2,
        "candidate_scores": 3,
        "missingness": 4,
    }

    ordered = sorted(
        evidence_results,
        key=lambda item: preferred_order.get(item.get("kind"), 99),
    )
    for item in ordered:
        if len(charts) >= max_charts:
            break
        if not item.get("success", True):
            continue
        kind = item.get("kind")
        if kind == "numeric_summary":
            continue

        data = _chart_data(item.get("data", []))
        if not data:
            continue

        chart_type = "bar"
        if kind == "distribution" and 2 <= len(data) <= 6:
            chart_type = "donut"

        charts.append({
            "id": item.get("id"),
            "type": chart_type,
            "title": item.get("title"),
            "subtitle": item.get("description", ""),
            "data": data[:10],
            "unit": item.get("unit", ""),
            "source_evidence_id": item.get("id"),
        })

    return charts
