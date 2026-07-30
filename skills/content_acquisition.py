"""Ethical HTTP fallback for public structured content.

This is deliberately a narrow fallback: it reads only public JSON-LD exposed
by a page and is invoked only after browser extraction fails the Task Contract.
It does not transfer browser authentication, evade access controls, or probe
undocumented endpoints.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from html.parser import HTMLParser
import json
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse

from skills.dpcli_task_contract import evaluate_contract_items, result_items


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_json_ld = False
        self.payloads: list[str] = []
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = {str(key).lower(): str(value).lower() for key, value in attrs}
        self._in_json_ld = (
            tag.lower() == "script"
            and attributes.get("type") == "application/ld+json"
        )
        if self._in_json_ld:
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._in_json_ld and tag.lower() == "script":
            self.payloads.append("".join(self._parts))
            self._parts = []
            self._in_json_ld = False


@dataclass(frozen=True)
class ContentAcquisitionResult:
    items: list[dict[str, Any]]
    source: str = ""
    reason: str = ""
    status_code: int | None = None


def _json_ld_objects(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _json_ld_objects(item)
        else:
            yield value
    elif isinstance(value, list):
        for item in value:
            yield from _json_ld_objects(item)


def _author(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("name") or "").strip()
    if isinstance(value, list):
        return ", ".join(filter(None, (_author(item) for item in value)))
    return str(value or "").strip()


def _to_item(value: Mapping[str, Any], source_url: str) -> dict[str, Any]:
    offers = value.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    offers = offers if isinstance(offers, Mapping) else {}
    return {
        "title": str(
            value.get("name") or value.get("headline") or value.get("title") or ""
        ).strip(),
        "url": str(value.get("url") or source_url).strip(),
        "description": str(value.get("description") or "").strip(),
        "author": _author(value.get("author")),
        "price": str(value.get("price") or offers.get("price") or "").strip(),
        "text": str(value.get("articleBody") or value.get("description") or "").strip(),
        "tags": value.get("keywords") or value.get("about") or [],
    }


class SessionStructuredAcquirer:
    """Public SessionPage adapter, guarded by the same Site Policy as browser work."""

    def __init__(
        self,
        *,
        page_factory: Callable[[], Any] | None = None,
        site_policy=None,
        timeout_seconds: int = 10,
    ) -> None:
        self._page_factory = page_factory
        self.site_policy = site_policy
        self.timeout_seconds = max(1, int(timeout_seconds))

    def _new_page(self):
        if self._page_factory is not None:
            return self._page_factory()
        from DrissionPage import SessionPage

        return SessionPage()

    def acquire(self, url: str, *, schema: Iterable[str]) -> ContentAcquisitionResult:
        parsed = urlparse(str(url or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ContentAcquisitionResult([], reason="invalid_url")
        if self.site_policy is not None:
            decision = self.site_policy.authorize(url)
            if not decision.allowed:
                return ContentAcquisitionResult([], reason="site_policy_denied")
        try:
            page = self._new_page()
            connected = bool(
                page.get(url, timeout=self.timeout_seconds, retry=0, interval=0)
            )
        except Exception as exc:
            return ContentAcquisitionResult([], reason=f"session_error:{type(exc).__name__}")
        response = getattr(page, "response", None)
        status_code = getattr(response, "status_code", None)
        headers = getattr(response, "headers", None)
        if self.site_policy is not None and hasattr(self.site_policy, "observe_response"):
            signal = self.site_policy.observe_response(
                url,
                status_code=status_code,
                headers=headers if isinstance(headers, Mapping) else None,
            )
            if signal.detected:
                return ContentAcquisitionResult(
                    [], reason=f"site_blocked:{signal.kind}", status_code=status_code
                )
        if not connected:
            return ContentAcquisitionResult([], reason="session_unavailable", status_code=status_code)
        parser = _JsonLdParser()
        parser.feed(str(getattr(page, "html", "") or ""))
        requested_fields = [str(field) for field in schema if str(field).strip()]
        items: list[dict[str, Any]] = []
        for payload in parser.payloads:
            try:
                decoded = json.loads(payload)
            except (TypeError, ValueError):
                continue
            for value in _json_ld_objects(decoded):
                item = _to_item(value, str(getattr(page, "url", "") or url))
                projected = {
                    field: item.get(field)
                    for field in requested_fields
                    if field in item
                }
                if any(value not in (None, "", []) for value in projected.values()):
                    items.append(projected)
        return ContentAcquisitionResult(items, source="session_json_ld", status_code=status_code)


def enrich_extract_result_from_session(
    state: Mapping[str, Any],
    action: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    acquirer: SessionStructuredAcquirer | None = None,
) -> dict[str, Any]:
    """Replace a failing browser extraction only with a contract-valid fallback."""
    if (
        not isinstance(result, Mapping)
        or not result.get("ok")
        or str(action.get("skill") or "").lower() != "extract"
    ):
        return dict(result or {})
    contract = state.get("dpcli_task_contract")
    if not isinstance(contract, Mapping) or not contract.get("schema"):
        return dict(result)
    params = action.get("params") if isinstance(action.get("params"), Mapping) else {}
    expected_count = max(1, int(params.get("limit") or contract.get("min_items") or 1))
    if evaluate_contract_items(
        dict(contract), result_items(dict(result)), expected_count=expected_count
    )["is_success"]:
        return dict(result)
    source_url = str(state.get("current_url") or "")
    if not source_url:
        return dict(result)
    if acquirer is None:
        from skills.site_policy import site_policy

        acquirer = SessionStructuredAcquirer(site_policy=site_policy)
    acquired = acquirer.acquire(source_url, schema=contract.get("schema") or [])
    if not acquired.items or not evaluate_contract_items(
        dict(contract), acquired.items, expected_count=expected_count
    )["is_success"]:
        return dict(result)
    enriched = deepcopy(dict(result))
    data = enriched.setdefault("data", {})
    data["items"] = acquired.items[:expected_count]
    data["item_count"] = len(data["items"])
    data["acquisition"] = {
        "source": acquired.source,
        "status_code": acquired.status_code,
    }
    return enriched
