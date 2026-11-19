"""Map Dynatrace activation endpoint fields to internal query definitions."""

import json
from typing import Any, Dict, List


def _parse_key_fields(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(field).strip() for field in raw if str(field).strip()]
    text = str(raw).strip()
    if not text:
        return []
    return [field.strip() for field in text.split(",") if field.strip()]


def _coerce_int(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)


def structured_query_to_internal(item: Dict[str, Any]) -> Dict[str, Any]:
    name = item.get("name")
    collection = item.get("collection")
    if not name:
        raise ValueError("Each query must have a name")
    if not collection:
        raise ValueError(f"Query '{name}' is missing collection")

    query: Dict[str, Any] = {
        "name": name,
        "enabled": bool(item.get("enabled", True)),
        "run_immediately": bool(item.get("run_immediately", False)),
        "frequency_minutes": _coerce_int(
            item.get("frequency_minutes_value", item.get("frequency_minutes")),
            1,
        ),
        "collection": collection,
        "pipeline": item.get("pipeline", "[]"),
    }

    if "align_to_scheduled_utc_hour" in item:
        query["align_to_scheduled_utc_hour"] = item["align_to_scheduled_utc_hour"]
    if item.get("schedule_offset_minutes") is not None:
        query["schedule_offset_minutes"] = item["schedule_offset_minutes"]

    result_mode = item.get("result_mode")
    if result_mode and str(result_mode).strip():
        query["result_mode"] = str(result_mode).strip()

    dedup_enabled = item.get("dedup_enabled", True)
    if dedup_enabled:
        query["dedup"] = {
            "enabled": True,
            "mode": item.get("dedup_mode", "send_on_change"),
            "key_fields": _parse_key_fields(item.get("dedup_key_fields")),
        }
    else:
        query["dedup"] = {"enabled": False}

    return query


def parse_queries_from_endpoint(endpoint: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Prefer structured ``queries`` list from activation UI.
    Fall back to legacy ``queries_json`` text blob when the list is empty.
    """
    structured = endpoint.get("queries")
    if isinstance(structured, list) and len(structured) > 0:
        return [structured_query_to_internal(item) for item in structured]

    raw = endpoint.get("queries_json", "[]")
    if isinstance(raw, list):
        queries = raw
    else:
        text = str(raw).strip()
        if not text or text == "[]":
            return []
        queries = json.loads(text)

    if not isinstance(queries, list):
        raise ValueError("queries_json must be a JSON array")

    return queries
