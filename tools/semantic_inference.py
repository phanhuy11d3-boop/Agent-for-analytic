import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


TAXONOMY_PATH = Path(__file__).with_name("semantic_taxonomy.json")


def _tokens(name: str) -> set[str]:
    return {part for part in re.split(r"[^a-z0-9]+", name.lower()) if part}


@lru_cache(maxsize=1)
def load_taxonomy() -> dict[str, Any]:
    with TAXONOMY_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _matches_name(name: str, rule: dict[str, Any]) -> float:
    lower = name.lower()
    parts = _tokens(lower)
    score = 0.0

    if lower in rule.get("exact", []):
        score += 0.75
    if any(lower.startswith(prefix) for prefix in rule.get("prefixes", [])):
        score += 0.45
    if any(lower.endswith(suffix) for suffix in rule.get("suffixes", [])):
        score += 0.45
    if parts & set(rule.get("tokens", [])):
        score += 0.4
    elif any(token in lower for token in rule.get("tokens", [])):
        score += 0.25

    return min(score, 1.0)


def _matches_type(data_type: str, rule: dict[str, Any]) -> float:
    dtype = (data_type or "").lower()
    if not dtype:
        return 0.0
    return 0.25 if any(hint in dtype for hint in rule.get("type_hints", [])) else 0.0


def infer_column_roles(column: dict[str, Any]) -> list[dict[str, Any]]:
    taxonomy = load_taxonomy()
    name = str(column.get("name", ""))
    data_type = str(column.get("data_type", ""))
    distinct_count = column.get("distinct_count")
    non_null_count = column.get("non_null_count")
    row_count = column.get("row_count") or 0

    candidates = []
    for role, rule in taxonomy.get("roles", {}).items():
        score = _matches_name(name, rule) + _matches_type(data_type, rule)

        if role == "identifier" and row_count and distinct_count == row_count:
            score += 0.25
        if role == "dimension" and isinstance(distinct_count, int) and 1 < distinct_count <= 30:
            score += 0.25
        if role == "dimension" and isinstance(distinct_count, int) and distinct_count > 30:
            if _matches_type(data_type, {"type_hints": ["integer", "numeric", "double", "real", "decimal"]}):
                score -= 0.2
        if role == "outcome" and isinstance(distinct_count, int) and 1 < distinct_count <= 3:
            score += 0.25
        if role == "binary_flag" and isinstance(distinct_count, int) and 1 < distinct_count <= 3:
            score += 0.25
        if role == "text" and non_null_count and isinstance(distinct_count, int) and distinct_count > 30:
            score += 0.1

        score = round(min(score, 1.0), 3)
        if score >= 0.25:
            candidates.append({"role": role, "confidence": score})

    candidates.sort(key=lambda item: item["confidence"], reverse=True)
    return candidates


def best_role(column: dict[str, Any], fallback: str = "attribute") -> str:
    roles = column.get("role_candidates") or infer_column_roles(column)
    return roles[0]["role"] if roles else fallback


def is_binary_like(values: list[Any]) -> bool:
    clean = {str(v).strip().lower() for v in values if v is not None and str(v).strip() != ""}
    if not clean or len(clean) > 3:
        return False

    binary_values = load_taxonomy().get("binary_values", {})
    known = set(binary_values.get("positive", [])) | set(binary_values.get("negative", []))
    return clean <= known or len(clean) == 2
