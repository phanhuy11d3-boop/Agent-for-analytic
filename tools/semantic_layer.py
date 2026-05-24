from typing import Any

from tools.context_store import schema_fingerprint
from tools.mschema_builder import build_mschema


def build_semantic_layer(
    table_profile: dict[str, Any],
    semantic_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = semantic_context or {}
    return {
        "version": "semantic-layer-v1",
        "schema_fingerprint": schema_fingerprint(table_profile),
        "status": "confirmed" if context.get("confirmed") else "draft",
        "table_name": table_profile.get("table_name") or context.get("table_name"),
        "table_purpose": context.get("table_purpose", ""),
        "row_grain": context.get("row_grain", ""),
        "primary_metric": context.get("primary_metric", ""),
        "outcome_column": context.get("outcome_column", ""),
        "positive_outcome_value": context.get("positive_outcome_value", ""),
        "negative_outcome_value": context.get("negative_outcome_value", ""),
        "mschema": build_mschema(table_profile, context),
    }
