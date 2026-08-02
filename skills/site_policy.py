"""Ethical site-access policy with robots, pacing, and block detection."""

from __future__ import annotations

import ipaddress
import json
from pathlib import Path
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse


@dataclass(frozen=True)
class SitePolicyConfig:
    enabled: bool = True
    robots_enabled: bool = True
    robots_fail_open: bool = True
    allow_private: bool = False
    min_interval_seconds: float = 0.5
    robots_timeout_seconds: float = 5.0
    user_agent: str = "AutoWeb/6"
    robots_cache_seconds: float = 600.0
    # A SQLite ledger makes per-domain policy survive restarts and coordinate
    # concurrent Task Runs without depending on a particular worker process.
    access_ledger_path: str = ""
    max_requests_per_domain: int = 240
    request_window_seconds: float = 3600.0
    cooldown_seconds: float = 300.0


@dataclass(frozen=True)
class SitePolicyDecision:
    allowed: bool
    reason: str
    url: str
    domain: str
    crawl_delay_seconds: float = 0.0
    waited_seconds: float = 0.0
    robots_checked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BlockingSignal:
    detected: bool
    kind: str = ""
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _LedgerReservation:
    allowed: bool
    reason: str = "allowed"
    waited_seconds: float = 0.0


class SiteAccessLedger:
    """Small durable, cross-process per-domain access ledger."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _ensure_schema(self) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS site_access_ledger (
                    domain TEXT PRIMARY KEY,
                    window_started REAL NOT NULL,
                    request_count INTEGER NOT NULL,
                    last_access REAL NOT NULL,
                    cooldown_until REAL NOT NULL DEFAULT 0
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    def reserve(
        self,
        domain: str,
        *,
        now: float,
        interval_seconds: float,
        max_requests: int,
        window_seconds: float,
    ) -> _LedgerReservation:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT window_started, request_count, last_access, cooldown_until
                FROM site_access_ledger WHERE domain = ?
                """,
                (domain,),
            ).fetchone()
            if row is None:
                window_started, request_count, last_access, cooldown_until = (
                    now,
                    0,
                    0.0,
                    0.0,
                )
            else:
                window_started, request_count, last_access, cooldown_until = row
            if now < float(cooldown_until or 0.0):
                return _LedgerReservation(False, "domain_cooldown")
            if now - float(window_started) >= max(1.0, float(window_seconds)):
                window_started, request_count = now, 0
            if max_requests > 0 and int(request_count) >= int(max_requests):
                return _LedgerReservation(False, "domain_request_budget_exhausted")
            waited = max(0.0, float(interval_seconds) - (now - float(last_access)))
            scheduled = now + waited
            connection.execute(
                """
                INSERT INTO site_access_ledger(
                    domain, window_started, request_count, last_access, cooldown_until
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    window_started = excluded.window_started,
                    request_count = excluded.request_count,
                    last_access = excluded.last_access,
                    cooldown_until = excluded.cooldown_until
                """,
                (
                    domain,
                    float(window_started),
                    int(request_count) + 1,
                    scheduled,
                    float(cooldown_until or 0.0),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return _LedgerReservation(True, waited_seconds=waited)

    def impose_cooldown(self, domain: str, *, until: float, now: float) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT window_started, request_count, last_access, cooldown_until
                FROM site_access_ledger WHERE domain = ?
                """,
                (domain,),
            ).fetchone()
            if row is None:
                window_started, request_count, last_access, current_cooldown = (
                    now,
                    0,
                    0.0,
                    0.0,
                )
            else:
                window_started, request_count, last_access, current_cooldown = row
            connection.execute(
                """
                INSERT INTO site_access_ledger(
                    domain, window_started, request_count, last_access, cooldown_until
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    window_started = excluded.window_started,
                    request_count = excluded.request_count,
                    last_access = excluded.last_access,
                    cooldown_until = excluded.cooldown_until
                """,
                (
                    domain,
                    float(window_started),
                    int(request_count),
                    float(last_access),
                    max(float(current_cooldown or 0.0), float(until)),
                ),
            )
            connection.commit()
        finally:
            connection.close()


class SitePolicy:
    """Stateful rate adapter around a deterministic site policy."""

    _BLOCK_PATTERNS = {
        "access_denied": (
            "403 forbidden",
            "access denied",
            "permission denied",
            "request blocked",
            "访问被拒绝",
            "拒绝访问",
        ),
        "captcha": (
            "captcha",
            "verify you are human",
            "robot check",
            "whaleguard block",
            "\u4eba\u673a\u9a8c\u8bc1",
            "\u5b89\u5168\u9a8c\u8bc1",
        ),
        "rate_limit": (
            "too many requests",
            "rate limit",
            "http 429",
            "\u8bf7\u6c42\u8fc7\u4e8e\u9891\u7e41",
            "\u64cd\u4f5c\u9891\u7e41",
        ),
        "login_required": (
            "login required",
            "sign in to continue",
            "京东-欢迎登录",
            "\u8bf7\u5148\u767b\u5f55\u540e\u7ee7\u7eed",
            "\u8bf7\u767b\u5f55\u540e\u7ee7\u7eed",
            "\u767b\u5f55\u540e\u67e5\u770b",
        ),
        "paywall": (
            "subscribe to continue",
            "subscription required",
            "\u8ba2\u9605\u540e\u9605\u8bfb",
            "\u4ed8\u8d39\u540e\u67e5\u770b",
        ),
    }

    def __init__(
        self,
        config: SitePolicyConfig | None = None,
        *,
        opener: Callable[..., Any] | None = None,
        monotonic: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        ledger: SiteAccessLedger | None = None,
    ) -> None:
        self.config = config or SitePolicyConfig()
        self._opener = opener or urllib.request.urlopen
        self._monotonic = monotonic or time.monotonic
        self._wall_clock = wall_clock or time.time
        self._sleeper = sleeper or time.sleep
        self._robots: dict[
            str,
            tuple[float, urllib.robotparser.RobotFileParser | None, str],
        ] = {}
        self._last_access: dict[str, float] = {}
        self._lock = threading.RLock()
        self._ledger = ledger or (
            SiteAccessLedger(self.config.access_ledger_path)
            if self.config.access_ledger_path
            else None
        )

    @staticmethod
    def _private_host(host: str) -> bool:
        normalized = str(host or "").strip().lower().rstrip(".")
        if normalized in {"localhost", "localhost.localdomain"}:
            return True
        try:
            address = ipaddress.ip_address(normalized)
        except ValueError:
            return False
        return bool(
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        )

    def _robots_parser(
        self,
        parsed,
    ) -> tuple[urllib.robotparser.RobotFileParser | None, str, bool]:
        origin = f"{parsed.scheme}://{parsed.netloc}"
        now = self._monotonic()
        cached = self._robots.get(origin)
        if cached and now - cached[0] <= self.config.robots_cache_seconds:
            return cached[1], cached[2], True

        robots_url = f"{origin}/robots.txt"
        request = urllib.request.Request(
            robots_url,
            headers={"User-Agent": self.config.user_agent},
        )
        try:
            with self._opener(
                request,
                timeout=self.config.robots_timeout_seconds,
            ) as response:
                raw = response.read(512_000)
                charset = response.headers.get_content_charset() or "utf-8"
                text = raw.decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code in {404, 410}:
                self._robots[origin] = (now, None, "robots_missing")
                return None, "robots_missing", True
            self._robots[origin] = (now, None, f"robots_http_{exc.code}")
            return None, f"robots_http_{exc.code}", True
        except Exception as exc:
            reason = f"robots_unavailable:{type(exc).__name__}"
            self._robots[origin] = (now, None, reason)
            return None, reason, True

        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(text.splitlines())
        self._robots[origin] = (now, parser, "robots_loaded")
        return parser, "robots_loaded", True

    def authorize(self, url: str, *, pace: bool = True) -> SitePolicyDecision:
        text = str(url or "").strip()
        try:
            parsed = urlparse(text)
        except ValueError:
            return SitePolicyDecision(False, "invalid_url", text, "")
        domain = str(parsed.hostname or "").lower()
        if not self.config.enabled:
            return SitePolicyDecision(True, "policy_disabled", text, domain)
        if parsed.scheme not in {"http", "https"} or not domain:
            return SitePolicyDecision(False, "http_https_required", text, domain)
        if parsed.username or parsed.password:
            return SitePolicyDecision(False, "embedded_credentials", text, domain)
        if self._private_host(domain) and not self.config.allow_private:
            return SitePolicyDecision(False, "private_network_denied", text, domain)

        robots_checked = False
        crawl_delay = 0.0
        if self.config.robots_enabled:
            parser, robots_reason, robots_checked = self._robots_parser(parsed)
            if parser is not None:
                if not parser.can_fetch(self.config.user_agent, text):
                    return SitePolicyDecision(
                        False,
                        "robots_denied",
                        text,
                        domain,
                        robots_checked=True,
                    )
                crawl_delay = float(
                    parser.crawl_delay(self.config.user_agent)
                    or parser.crawl_delay("*")
                    or 0.0
                )
            elif (
                not self.config.robots_fail_open
                and robots_reason != "robots_missing"
            ):
                return SitePolicyDecision(
                    False,
                    robots_reason,
                    text,
                    domain,
                    robots_checked=True,
                )

        interval = max(
            float(self.config.min_interval_seconds),
            crawl_delay,
        )
        waited = 0.0
        if self._ledger is not None:
            reservation = self._ledger.reserve(
                domain,
                now=self._wall_clock(),
                interval_seconds=interval if pace else 0.0,
                max_requests=int(self.config.max_requests_per_domain),
                window_seconds=float(self.config.request_window_seconds),
            )
            if not reservation.allowed:
                return SitePolicyDecision(
                    False,
                    reservation.reason,
                    text,
                    domain,
                    crawl_delay_seconds=crawl_delay,
                    robots_checked=robots_checked,
                )
            waited = reservation.waited_seconds
            if waited:
                self._sleeper(waited)
        elif pace and interval > 0:
            with self._lock:
                now = self._monotonic()
                last = self._last_access.get(domain)
                if last is not None:
                    waited = max(0.0, interval - (now - last))
                    if waited:
                        self._sleeper(waited)
                        now = self._monotonic()
                self._last_access[domain] = now
        return SitePolicyDecision(
            True,
            "allowed",
            text,
            domain,
            crawl_delay_seconds=crawl_delay,
            waited_seconds=waited,
            robots_checked=robots_checked,
        )

    def authorize_action(
        self,
        action: Mapping[str, Any],
    ) -> list[SitePolicyDecision]:
        skill = str(action.get("skill") or "").strip().lower()
        params = action.get("params") or {}
        if not isinstance(params, Mapping):
            return []
        urls: list[str] = []
        if skill == "open":
            urls.append(str(params.get("url") or ""))
        elif skill == "batch-detail-extract":
            for item in params.get("items") or []:
                if isinstance(item, Mapping):
                    urls.append(
                        str(
                            item.get("detail_url")
                            or item.get("url")
                            or item.get("href")
                            or ""
                        )
                    )
        decisions = []
        seen_domains = set()
        for url in urls:
            domain = str(urlparse(url).hostname or "").lower()
            decision = self.authorize(
                url,
                pace=domain not in seen_domains,
            )
            decisions.append(decision)
            seen_domains.add(domain)
            if not decision.allowed:
                break
        return decisions

    def observe_response(
        self,
        url: str,
        *,
        status_code: int | None = None,
        headers: Mapping[str, Any] | None = None,
    ) -> BlockingSignal:
        """Persist an explicit response-level restriction and stop the run."""
        if int(status_code or 0) != 429:
            return BlockingSignal(False)
        domain = str(urlparse(str(url or "")).hostname or "").lower()
        retry_after = 0.0
        for key, value in (headers or {}).items():
            if str(key).lower() == "retry-after":
                try:
                    retry_after = max(0.0, float(value))
                except (TypeError, ValueError):
                    retry_after = 0.0
                break
        cooldown = max(float(self.config.cooldown_seconds), retry_after)
        if domain and self._ledger is not None:
            now = self._wall_clock()
            self._ledger.impose_cooldown(domain, until=now + cooldown, now=now)
        return BlockingSignal(True, "rate_limit", "http_status:429")

    def detect_block_signal(self, payload: Any) -> BlockingSignal:
        if isinstance(payload, str):
            text = payload
        else:
            try:
                text = json.dumps(payload, ensure_ascii=False, default=str)
            except Exception:
                text = str(payload)
        lowered = text.lower()
        for kind, patterns in self._BLOCK_PATTERNS.items():
            for pattern in patterns:
                if pattern.lower() in lowered:
                    return BlockingSignal(True, kind, pattern)
        return BlockingSignal(False)


def build_site_policy() -> SitePolicy:
    from config import (
        SITE_POLICY_ALLOW_PRIVATE,
        SITE_POLICY_ACCESS_LEDGER_PATH,
        SITE_POLICY_COOLDOWN_SECONDS,
        SITE_POLICY_ENABLED,
        SITE_POLICY_MAX_REQUESTS_PER_DOMAIN,
        SITE_POLICY_MIN_INTERVAL_SECONDS,
        SITE_POLICY_REQUEST_WINDOW_SECONDS,
        SITE_POLICY_ROBOTS_ENABLED,
        SITE_POLICY_ROBOTS_FAIL_OPEN,
        SITE_POLICY_ROBOTS_TIMEOUT_SECONDS,
        SITE_POLICY_USER_AGENT,
    )

    return SitePolicy(
        SitePolicyConfig(
            enabled=SITE_POLICY_ENABLED,
            robots_enabled=SITE_POLICY_ROBOTS_ENABLED,
            robots_fail_open=SITE_POLICY_ROBOTS_FAIL_OPEN,
            allow_private=SITE_POLICY_ALLOW_PRIVATE,
            min_interval_seconds=SITE_POLICY_MIN_INTERVAL_SECONDS,
            robots_timeout_seconds=SITE_POLICY_ROBOTS_TIMEOUT_SECONDS,
            user_agent=SITE_POLICY_USER_AGENT,
            access_ledger_path=SITE_POLICY_ACCESS_LEDGER_PATH,
            max_requests_per_domain=SITE_POLICY_MAX_REQUESTS_PER_DOMAIN,
            request_window_seconds=SITE_POLICY_REQUEST_WINDOW_SECONDS,
            cooldown_seconds=SITE_POLICY_COOLDOWN_SECONDS,
        )
    )


site_policy = build_site_policy()
