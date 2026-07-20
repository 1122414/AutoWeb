from __future__ import annotations

from skills.site_policy import SitePolicy, SitePolicyConfig


class _Headers:
    @staticmethod
    def get_content_charset():
        return "utf-8"


class _Response:
    headers = _Headers()

    def __init__(self, text):
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _limit):
        return self._text.encode()


def _policy():
    clock = [10.0]

    def sleep(seconds):
        clock[0] += seconds

    robots = """
User-agent: *
Disallow: /private
Crawl-delay: 2
"""
    policy = SitePolicy(
        SitePolicyConfig(
            min_interval_seconds=0.5,
            user_agent="AutoWeb/6",
        ),
        opener=lambda *_args, **_kwargs: _Response(robots),
        monotonic=lambda: clock[0],
        sleeper=sleep,
    )
    return policy, clock


def test_robots_and_crawl_delay_are_enforced_and_cached():
    policy, clock = _policy()

    first = policy.authorize("https://public.example/products")
    second = policy.authorize("https://public.example/next")
    denied = policy.authorize("https://public.example/private/data")

    assert first.allowed is True
    assert first.robots_checked is True
    assert second.allowed is True
    assert second.waited_seconds == 2
    assert clock[0] == 12
    assert denied.allowed is False
    assert denied.reason == "robots_denied"
    assert len(policy._robots) == 1


def test_private_network_and_embedded_credentials_are_denied():
    policy = SitePolicy(
        SitePolicyConfig(robots_enabled=False, min_interval_seconds=0)
    )

    assert (
        policy.authorize("http://127.0.0.1/admin").reason
        == "private_network_denied"
    )
    assert (
        policy.authorize("https://user:secret@example.com").reason
        == "embedded_credentials"
    )


def test_blocking_signals_stop_instead_of_attempting_bypass():
    policy = SitePolicy(SitePolicyConfig(robots_enabled=False))

    captcha = policy.detect_block_signal(
        {"title": "Verify you are human - CAPTCHA"}
    )
    rate = policy.detect_block_signal({"error": "HTTP 429 Too Many Requests"})

    assert captcha.detected is True
    assert captcha.kind == "captcha"
    assert rate.detected is True
    assert rate.kind == "rate_limit"
