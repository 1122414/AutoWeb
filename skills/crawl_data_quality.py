"""Record-level validation and provenance for crawler output.

The agent keeps extraction permissive, but never silently presents malformed
values as verified data. This module has no browser dependency so it can be
used identically by deterministic and LLM-assisted extraction paths.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse


_TEXT_FIELDS = {
    "title",
    "name",
    "description",
    "text",
    "author",
    "team",
}
_INTEGER_FIELDS = {"year", "wins", "losses", "count", "rank"}


def is_valid_field_value(field: str, value: Any) -> bool:
    """Return whether a populated value is plausible for its semantic field."""
    name = str(field or "").strip().lower()
    if value is None or value is False:
        return False
    if name in _TEXT_FIELDS:
        text = str(value).strip()
        if not text:
            return False
        if name in {"title", "name"}:
            parsed = urlparse(text)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                return False
            if re.search(r"<\s*/?\s*[a-z][^>]*>", text, re.IGNORECASE):
                return False
        return True
    if name in {"url", "href", "detail_url", "link"}:
        parsed = urlparse(str(value).strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    if name == "price":
        text = str(value).strip().replace(",", "")
        return bool(re.fullmatch(r"(?:[$€£¥]\s*)?\d+(?:\.\d{1,2})?", text))
    if name in _INTEGER_FIELDS:
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            return False
        if name == "year":
            return 1000 <= number <= 2100
        return number >= 0
    if name == "tags":
        if isinstance(value, (list, tuple, set)):
            return any(str(item).strip() for item in value)
        return bool(str(value).strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return bool(str(value).strip())


def item_quality(item: Mapping[str, Any], schema: Iterable[str]) -> dict[str, Any]:
    """Produce a compact, export-safe assessment for one record."""
    valid_fields: dict[str, bool] = {}
    issues: list[dict[str, str]] = []
    for raw_field in schema:
        field = str(raw_field or "").strip()
        if not field:
            continue
        value = item.get(field)
        valid = is_valid_field_value(field, value)
        valid_fields[field] = valid
        if not valid:
            issues.append({"field": field, "reason": "missing_or_invalid"})
    return {"valid_fields": valid_fields, "issues": issues}


def annotate_result_provenance(
    state: Mapping[str, Any],
    action: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Attach source and field-quality evidence to successful data results."""
    if not isinstance(result, Mapping) or not result.get("ok"):
        return result if isinstance(result, dict) else dict(result or {})
    skill = str(action.get("skill") or result.get("action") or "").strip().lower()
    if skill not in {"extract", "list-items", "batch-detail-extract"}:
        return result if isinstance(result, dict) else dict(result)
    enriched = deepcopy(dict(result))
    data = enriched.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return result if isinstance(result, dict) else enriched
    params = action.get("params") if isinstance(action.get("params"), Mapping) else {}
    schema = [str(field) for field in (params.get("schema") or []) if str(field).strip()]
    if not schema:
        return result if isinstance(result, dict) else enriched
    page = data.get("page") if isinstance(data.get("page"), Mapping) else {}
    source_url = str(page.get("url") or state.get("current_url") or "")
    snapshot_ref = state.get("dpcli_snapshot_ref")
    snapshot_ref = snapshot_ref if isinstance(snapshot_ref, Mapping) else {}
    snapshot_id = str(
        snapshot_ref.get("snapshot_id")
        or snapshot_ref.get("content_hash")
        or snapshot_ref.get("index_file")
        or ""
    )
    observed_at = captured_at or datetime.now(timezone.utc).isoformat()
    annotated = []
    for raw_item in data["items"]:
        if not isinstance(raw_item, Mapping):
            annotated.append(raw_item)
            continue
        item = dict(raw_item)
        item["_provenance"] = {
            "source_url": source_url,
            "snapshot_id": snapshot_id,
            "action": skill,
            "captured_at": observed_at,
        }
        item["_quality"] = item_quality(item, schema)
        annotated.append(item)
    data["items"] = annotated
    return enriched
