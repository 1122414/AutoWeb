from __future__ import annotations

from dataclasses import dataclass

from skills.content_acquisition import (
    ContentAcquisitionResult,
    SessionStructuredAcquirer,
    enrich_extract_result_from_session,
)


@dataclass
class _Decision:
    allowed: bool = True


class _Policy:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.urls: list[str] = []

    def authorize(self, url: str):
        self.urls.append(url)
        return _Decision(self.allowed)

    def observe_response(self, *_args, **_kwargs):
        return type("Signal", (), {"detected": False})()


class _Page:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.html = """
        <script type="application/ld+json">
        {"@graph": [{"@type": "Product", "name": "Book A",
          "url": "https://example.test/a", "offers": {"price": "12.50"}}]}
        </script>
        """
        self.response = type("Response", (), {"status_code": 200, "headers": {}})()

    def get(self, url: str, timeout: int, retry: int, interval: int) -> bool:
        self.calls.append((url, timeout))
        return True


def test_session_acquirer_extracts_visible_json_ld_after_policy_authorization():
    page = _Page()
    policy = _Policy()
    acquirer = SessionStructuredAcquirer(page_factory=lambda: page, site_policy=policy)

    result = acquirer.acquire(
        "https://example.test/products",
        schema=["title", "url", "price"],
    )

    assert result.source == "session_json_ld"
    assert result.items == [
        {"title": "Book A", "url": "https://example.test/a", "price": "12.50"}
    ]
    assert page.calls == [("https://example.test/products", 10)]
    assert policy.urls == ["https://example.test/products"]


def test_session_acquirer_does_not_make_a_request_when_policy_denies():
    page = _Page()
    acquirer = SessionStructuredAcquirer(
        page_factory=lambda: page,
        site_policy=_Policy(allowed=False),
    )

    result = acquirer.acquire("https://example.test/private", schema=["title"])

    assert result.items == []
    assert result.reason == "site_policy_denied"
    assert page.calls == []


def test_session_result_replaces_only_a_contract_failing_browser_result():
    result = enrich_extract_result_from_session(
        {
            "current_url": "https://example.test/products",
            "dpcli_task_contract": {
                "schema": ["title", "url", "price"],
                "min_items": 1,
            },
        },
        {"skill": "extract", "params": {"limit": 1}},
        {"ok": True, "action": "extract", "data": {"items": [{"title": "Book A"}]}},
        acquirer=type(
            "Acquirer",
            (),
            {
                "acquire": lambda *_args, **_kwargs: ContentAcquisitionResult(
                    items=[
                        {
                            "title": "Book A",
                            "url": "https://example.test/a",
                            "price": "12.50",
                        }
                    ],
                    source="session_json_ld",
                )
            },
        )(),
    )

    assert result["data"]["items"][0]["price"] == "12.50"
    assert result["data"]["acquisition"]["source"] == "session_json_ld"
