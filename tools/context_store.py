import hashlib
import json
from pathlib import Path
from typing import Any


STORE_PATH = Path(__file__).resolve().parent.parent / "semantic_contexts.json"


def schema_fingerprint(table_profile: dict[str, Any]) -> str:
    columns = [
        {
            "name": col.get("name", ""),
            "data_type": col.get("data_type", ""),
        }
        for col in table_profile.get("columns", [])
    ]
    payload = json.dumps(columns, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _empty_store() -> dict[str, Any]:
    return {"version": 2, "contexts": {}, "contexts_by_fingerprint": {}, "table_aliases": {}}


def _load_store() -> dict[str, Any]:
    if not STORE_PATH.exists():
        return _empty_store()
    try:
        with STORE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("contexts"), dict):
            data.setdefault("contexts_by_fingerprint", {})
            data.setdefault("table_aliases", {})
            for table_name, item in list(data.get("contexts", {}).items()):
                if isinstance(item, dict) and item.get("schema_fingerprint"):
                    data["contexts_by_fingerprint"].setdefault(item["schema_fingerprint"], item)
                    data["table_aliases"].setdefault(table_name, item["schema_fingerprint"])
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return _empty_store()


def _save_store(data: dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STORE_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    tmp_path.replace(STORE_PATH)


def default_context(table_name: str | None = None) -> dict[str, Any]:
    return {
        "table_name": table_name,
        "table_purpose": "",
        "row_grain": "",
        "primary_metric": "",
        "outcome_column": "",
        "positive_outcome_value": "",
        "negative_outcome_value": "",
        "column_descriptions": {},
        "confirmed": False,
    }


def load_context(table_name: str | None, table_profile: dict[str, Any]) -> dict[str, Any]:
    expected = schema_fingerprint(table_profile)

    store = _load_store()
    item = store.get("contexts_by_fingerprint", {}).get(expected)
    if not isinstance(item, dict) and table_name:
        item = store.get("contexts", {}).get(table_name)
    if not isinstance(item, dict):
        context = default_context(table_name)
        context["schema_fingerprint"] = expected
        return context

    if item.get("schema_fingerprint") != expected:
        context = default_context(table_name)
        context["stale_context_found"] = True
        context["expected_schema_fingerprint"] = expected
        context["stored_schema_fingerprint"] = item.get("schema_fingerprint")
        return context

    context = default_context(table_name)
    context.update(item.get("context") or {})
    context["table_name"] = table_name
    context["schema_fingerprint"] = expected
    return context


def save_context(table_name: str, table_profile: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    clean = default_context(table_name)
    clean.update({
        key: value for key, value in (context or {}).items()
        if key in clean
    })
    clean["table_name"] = table_name
    clean["confirmed"] = bool(clean.get("confirmed") or any(
        clean.get(key) for key in ("table_purpose", "row_grain", "primary_metric", "outcome_column")
    ))

    fingerprint = schema_fingerprint(table_profile)
    store = _load_store()
    item = {
        "schema_fingerprint": fingerprint,
        "context": clean,
    }
    store.setdefault("contexts_by_fingerprint", {})[fingerprint] = item
    store.setdefault("contexts", {})[table_name] = item
    store.setdefault("table_aliases", {})[table_name] = fingerprint
    _save_store(store)
    clean["schema_fingerprint"] = fingerprint
    return clean
