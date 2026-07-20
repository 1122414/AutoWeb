"""Unified cache admission policy across action, code, and DOM caches.

The cache backends remain adapters responsible for storage and retrieval.
This module owns the shared production decision seam: threshold, TTL,
same-run isolation, failure quarantine, and fingerprint compatibility.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class CacheCandidate:
    kind: str
    cache_id: str
    score: float
    created_at: str = ""
    fingerprint_version: str = ""
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class CacheDecision:
    allowed: bool
    reason: str
    candidate: CacheCandidate

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidate"]["metadata"] = dict(
            self.candidate.metadata or {}
        )
        return payload


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class CacheGovernance:
    """Pure policy object so all cache adapters receive identical decisions."""

    def __init__(
        self,
        *,
        ttl_hours: Mapping[str, float] | None = None,
        allow_legacy_fingerprint: bool = True,
        now=None,
    ) -> None:
        self.ttl_hours = {
            str(kind): max(0.0, float(hours))
            for kind, hours in (ttl_hours or {}).items()
        }
        self.allow_legacy_fingerprint = bool(allow_legacy_fingerprint)
        self._now = now or (lambda: datetime.now(UTC))

    def evaluate(
        self,
        candidate: CacheCandidate,
        *,
        threshold: float,
        failed_ids: Iterable[str] = (),
        task_started_at: datetime | str | None = None,
        required_fingerprint_version: str = "",
    ) -> CacheDecision:
        failed = {str(value) for value in failed_ids if str(value)}
        if candidate.cache_id and candidate.cache_id in failed:
            return CacheDecision(False, "failed_quarantine", candidate)
        if float(candidate.score) < float(threshold):
            return CacheDecision(False, "below_threshold", candidate)

        created_at = _parse_datetime(candidate.created_at)
        started_at = (
            task_started_at
            if isinstance(task_started_at, datetime)
            else _parse_datetime(task_started_at)
        )
        if created_at is not None and started_at is not None:
            normalized_start = (
                started_at.replace(tzinfo=UTC)
                if started_at.tzinfo is None
                else started_at.astimezone(UTC)
            )
            if created_at >= normalized_start:
                return CacheDecision(False, "same_run_write", candidate)

        ttl = self.ttl_hours.get(candidate.kind)
        if ttl and created_at is not None:
            if self._now() - created_at > timedelta(hours=ttl):
                return CacheDecision(False, "expired", candidate)

        required = str(required_fingerprint_version or "").strip()
        actual = str(candidate.fingerprint_version or "").strip()
        if required and actual != required:
            if actual or not self.allow_legacy_fingerprint:
                return CacheDecision(
                    False,
                    "fingerprint_version_mismatch",
                    candidate,
                )
        return CacheDecision(True, "eligible", candidate)

    def filter_hits(
        self,
        kind: str,
        hits: Sequence[Any],
        *,
        threshold: float,
        failed_ids: Iterable[str] = (),
        task_started_at: datetime | str | None = None,
        required_fingerprint_version: str = "",
    ) -> tuple[list[Any], list[CacheDecision]]:
        accepted: list[Any] = []
        decisions: list[CacheDecision] = []
        for hit in hits:
            fingerprint_version = str(
                getattr(hit, "fingerprint_version", "")
                or getattr(hit, "semantic_fingerprint_version", "")
                or ""
            )
            candidate = CacheCandidate(
                kind=str(kind),
                cache_id=str(getattr(hit, "id", "") or ""),
                score=float(getattr(hit, "score", 0.0) or 0.0),
                created_at=str(getattr(hit, "created_at", "") or ""),
                fingerprint_version=fingerprint_version,
                metadata={
                    "url_pattern": str(
                        getattr(hit, "url_pattern", "") or ""
                    ),
                },
            )
            decision = self.evaluate(
                candidate,
                threshold=threshold,
                failed_ids=failed_ids,
                task_started_at=task_started_at,
                required_fingerprint_version=required_fingerprint_version,
            )
            decisions.append(decision)
            if decision.allowed:
                accepted.append(hit)
        return accepted, decisions


def build_cache_governance() -> CacheGovernance:
    from config import (
        ACTION_CACHE_TTL_HOURS,
        CACHE_GOVERNANCE_ALLOW_LEGACY_FINGERPRINT,
        CODE_CACHE_TTL_HOURS,
        DOM_CACHE_TTL_HOURS,
    )

    return CacheGovernance(
        ttl_hours={
            "action": ACTION_CACHE_TTL_HOURS,
            "code": CODE_CACHE_TTL_HOURS,
            "dom": DOM_CACHE_TTL_HOURS,
        },
        allow_legacy_fingerprint=CACHE_GOVERNANCE_ALLOW_LEGACY_FINGERPRINT,
    )


cache_governance = build_cache_governance()
