from collections import Counter, defaultdict
from typing import Any


def _fmt_value(value: Any) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if num == int(num):
        return f"{int(num):,}"
    if abs(num) >= 1000:
        return f"{num:,.0f}"
    return f"{num:.3f}".rstrip("0").rstrip(".")


def _fmt_label(name: Any) -> str:
    return str(name or "").replace("_", " ").strip().title()


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _numeric_label(row: dict[str, Any]) -> float | None:
    return _as_float(row.get("label"))


def _numbered_observation(
    item: dict[str, Any],
    observation_type: str,
    claim: str,
    strength: str = "medium",
    numbers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": observation_type,
        "claim": claim,
        "strength": strength,
        "evidence_id": item.get("id") or item.get("title") or item.get("kind"),
        "numbers": numbers or {},
    }


def _distribution_observations(item: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        row for row in (item.get("data") or [])
        if row.get("label") is not None and _as_float(row.get("value")) is not None
    ]
    if len(rows) < 2:
        return []

    total = sum(float(row.get("value") or 0) for row in rows)
    if total <= 0:
        return []

    observations: list[dict[str, Any]] = []
    label = _fmt_label(item.get("primary_dimension") or item.get("title") or "value")
    by_count = sorted(rows, key=lambda row: float(row.get("value") or 0), reverse=True)
    top = by_count[0]
    top_value = float(top.get("value") or 0)
    top_share = top_value / total * 100

    numeric_rows = [
        {**row, "_num_label": _numeric_label(row), "_value": float(row.get("value") or 0)}
        for row in rows
        if _numeric_label(row) is not None
    ]
    numeric_rows.sort(key=lambda row: row["_num_label"])
    if len(numeric_rows) == len(rows):
        low = numeric_rows[0]["_num_label"]
        high = numeric_rows[-1]["_num_label"]
        observations.append(_numbered_observation(
            item,
            "range",
            f"{label} ranges from {_fmt_value(low)} to {_fmt_value(high)} in the returned distribution.",
            "medium",
            {"min": low, "max": high},
        ))

    observations.append(_numbered_observation(
        item,
        "mode",
        (
            f"The most frequent {label} value is {top.get('label')} with "
            f"{_fmt_value(top_value)} rows ({top_share:.1f}% of returned non-null rows)."
        ),
        "high",
        {"label": top.get("label"), "value": top_value, "share_pct": round(top_share, 1)},
    ))

    if len(by_count) > 1:
        second = by_count[1]
        second_value = float(second.get("value") or 0)
        if second_value > 0 and top_value / second_value >= 1.5:
            observations.append(_numbered_observation(
                item,
                "dominance",
                (
                    f"{top.get('label')} is {top_value / second_value:.1f}x as frequent as "
                    f"the next value, {second.get('label')} ({_fmt_value(second_value)} rows)."
                ),
                "high" if top_value / second_value >= 3 else "medium",
                {
                    "top_label": top.get("label"),
                    "top_value": top_value,
                    "second_label": second.get("label"),
                    "second_value": second_value,
                    "ratio": round(top_value / second_value, 2),
                },
            ))

    high_frequency = [
        row for row in by_count[1:8]
        if float(row.get("value") or 0) >= max(10.0, total * 0.03)
    ][:6]
    if high_frequency:
        values = ", ".join(
            f"{row.get('label')} ({_fmt_value(row.get('value'))})"
            for row in high_frequency
        )
        observations.append(_numbered_observation(
            item,
            "high_frequency_values",
            f"Other high-frequency {label} values include {values}.",
            "medium",
            {"values": [{"label": row.get("label"), "value": row.get("value")} for row in high_frequency]},
        ))

    rare_threshold = max(3.0, total * 0.002)
    rare_values = [
        row for row in sorted(rows, key=lambda row: float(row.get("value") or 0))
        if float(row.get("value") or 0) <= rare_threshold
    ][:5]
    if rare_values and len(rows) >= 8:
        values = ", ".join(
            f"{row.get('label')} ({_fmt_value(row.get('value'))})"
            for row in rare_values
        )
        observations.append(_numbered_observation(
            item,
            "rare_values",
            f"Rare {label} values in the returned distribution include {values}.",
            "medium",
            {"threshold": rare_threshold, "values": [{"label": row.get("label"), "value": row.get("value")} for row in rare_values]},
        ))

    if len(numeric_rows) >= 5:
        non_increasing = sum(
            1 for prev, cur in zip(numeric_rows, numeric_rows[1:])
            if cur["_value"] <= prev["_value"]
        )
        spikes = []
        for prev, cur, nxt in zip(numeric_rows, numeric_rows[1:], numeric_rows[2:]):
            if cur["_value"] >= max(prev["_value"], nxt["_value"]) * 1.25 and cur["_value"] >= total * 0.01:
                spikes.append(cur)
        if spikes:
            values = ", ".join(f"{row.get('label')} ({_fmt_value(row.get('value'))})" for row in spikes[:4])
            observations.append(_numbered_observation(
                item,
                "local_spikes",
                f"Local frequency spikes appear at {values} compared with neighboring values.",
                "medium",
                {"values": [{"label": row.get("label"), "value": row.get("value")} for row in spikes[:4]]},
            ))

        if non_increasing / max(1, len(numeric_rows) - 1) >= 0.6 and numeric_rows[0]["_value"] >= max(1, numeric_rows[-1]["_value"] * 5):
            observations.append(_numbered_observation(
                item,
                "decline",
                (
                    f"Counts generally decline as {label} increases; the first value has "
                    f"{_fmt_value(numeric_rows[0]['_value'])} rows versus {_fmt_value(numeric_rows[-1]['_value'])} at the last returned value."
                ),
                "medium",
                {"first_count": numeric_rows[0]["_value"], "last_count": numeric_rows[-1]["_value"]},
            ))

    return observations[:6]


def _metric_by_dimension_observations(item: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        {**row, "_value": _as_float(row.get("value"))}
        for row in (item.get("data") or [])
        if row.get("label") is not None and _as_float(row.get("value")) is not None
    ]
    if len(rows) < 2:
        return []
    rows.sort(key=lambda row: row["_value"], reverse=True)
    total = sum(row["_value"] for row in rows)
    metric = _fmt_label(item.get("metric") or item.get("unit") or "value")
    dimension = _fmt_label(item.get("primary_dimension") or "group")
    top, second = rows[0], rows[1]
    observations = [
        _numbered_observation(
            item,
            "top_group",
            f"{top.get('label')} ranks first for {metric} by {dimension} with {_fmt_value(top['_value'])}.",
            "high",
            {"label": top.get("label"), "value": top["_value"]},
        )
    ]
    if second["_value"] and top["_value"] / second["_value"] >= 1.25:
        observations.append(_numbered_observation(
            item,
            "gap",
            (
                f"The lead over {second.get('label')} is {_fmt_value(top['_value'] - second['_value'])}, "
                f"or {top['_value'] / second['_value']:.1f}x."
            ),
            "medium",
            {"top": top["_value"], "second": second["_value"], "ratio": round(top["_value"] / second["_value"], 2)},
        ))
    if total > 0:
        top3 = sum(row["_value"] for row in rows[:3])
        if top3 / total >= 0.55:
            observations.append(_numbered_observation(
                item,
                "concentration",
                f"The top 3 {dimension} values account for {top3 / total * 100:.1f}% of returned {metric}.",
                "medium",
                {"top3_share_pct": round(top3 / total * 100, 1)},
            ))
    negatives = [row for row in rows if row["_value"] < 0]
    if negatives:
        values = ", ".join(f"{row.get('label')} ({_fmt_value(row['_value'])})" for row in negatives[:4])
        observations.append(_numbered_observation(
            item,
            "negative_values",
            f"Negative {metric} values appear for {values}.",
            "medium",
            {"values": [{"label": row.get("label"), "value": row["_value"]} for row in negatives[:4]]},
        ))
    return observations[:5]


def _metric_by_two_dimensions_observations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    metric_items = [
        item for item in items
        if item.get("success", True)
        and item.get("kind") == "metric_by_two_dimensions"
        and item.get("data")
    ]

    winners_by_metric: dict[str, dict[str, dict[str, Any]]] = {}
    for item in metric_items:
        metric = str(item.get("metric") or item.get("title") or "value")
        primary = _fmt_label(item.get("primary_dimension") or "group")
        series = _fmt_label(item.get("series_dimension") or "series")
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        negatives = []
        for row in item.get("data") or []:
            value = _as_float(row.get("value"))
            if row.get("label") is None or row.get("series") is None or value is None:
                continue
            grouped[str(row.get("label"))].append({**row, "_value": value})
            if value < 0:
                negatives.append({**row, "_value": value})

        winners = {}
        for label, rows in grouped.items():
            winners[label] = max(rows, key=lambda row: row["_value"])
        winners_by_metric[metric] = winners

        counter = Counter(str(row.get("series")) for row in winners.values())
        if counter:
            winner, count = counter.most_common(1)[0]
            if count >= 2:
                observations.append(_numbered_observation(
                    item,
                    "repeated_winner",
                    f"{winner} leads {metric} in {count} of {len(winners)} {primary} groups.",
                    "medium",
                    {"winner": winner, "count": count, "groups": len(winners), "metric": metric},
                ))

        if winners:
            bits = [
                f"{label}: {row.get('series')} ({_fmt_value(row.get('_value'))})"
                for label, row in sorted(winners.items())[:6]
            ]
            observations.append(_numbered_observation(
                item,
                "winner_by_slice",
                f"Top {series} by {metric} within {primary}: " + "; ".join(bits) + ".",
                "medium",
                {"metric": metric},
            ))

        if negatives:
            values = ", ".join(
                f"{row.get('series')} in {row.get('label')} ({_fmt_value(row.get('_value'))})"
                for row in negatives[:4]
            )
            observations.append(_numbered_observation(
                item,
                "negative_values",
                f"Negative {metric} values appear for {values}.",
                "medium",
                {"metric": metric},
            ))

    if len(winners_by_metric) >= 2:
        first_metric, second_metric = list(winners_by_metric.keys())[:2]
        first = winners_by_metric[first_metric]
        second = winners_by_metric[second_metric]
        disagreements = []
        for label in sorted(set(first) & set(second)):
            first_winner = str(first[label].get("series"))
            second_winner = str(second[label].get("series"))
            if first_winner != second_winner:
                disagreements.append((label, first_winner, second_winner))
        if disagreements:
            bits = [
                f"{label}: {first_metric} leader {a}, {second_metric} leader {b}"
                for label, a, b in disagreements[:4]
            ]
            observations.append({
                "type": "metric_disagreement",
                "claim": "Metric leaders differ in " + "; ".join(bits) + ".",
                "strength": "medium",
                "evidence_id": ",".join(str(item.get("id")) for item in metric_items[:2]),
                "numbers": {"metrics": [first_metric, second_metric]},
            })

    return observations[:6]


def build_observations(
    evidence_results: list[dict[str, Any]],
    table_profile: dict[str, Any] | None = None,
    max_observations: int = 6,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    observations.extend(_metric_by_two_dimensions_observations(evidence_results))

    for item in evidence_results:
        if not item.get("success", True) or not item.get("data"):
            continue
        kind = item.get("kind")
        if kind == "distribution":
            observations.extend(_distribution_observations(item))
        elif kind in {"metric_by_dimension", "top_n", "outcome_rate_by_dimension"}:
            observations.extend(_metric_by_dimension_observations(item))

    seen = set()
    deduped = []
    for observation in observations:
        claim = observation.get("claim", "")
        if not claim or claim in seen:
            continue
        seen.add(claim)
        deduped.append(observation)
        if len(deduped) >= max_observations:
            break
    return deduped
