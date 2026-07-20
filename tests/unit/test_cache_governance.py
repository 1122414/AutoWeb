from __future__ import annotations

from collections import namedtuple
from datetime import UTC, datetime

from skills.cache_governance import CacheCandidate, CacheGovernance


Hit = namedtuple("Hit", "id score created_at url_pattern")


def _governance():
    return CacheGovernance(
        ttl_hours={"action": 24, "code": 48, "dom": 12},
        allow_legacy_fingerprint=False,
        now=lambda: datetime(2026, 7, 21, 12, tzinfo=UTC),
    )


def test_rejects_quarantined_expired_same_run_and_version_mismatch():
    governance = _governance()
    cases = [
        (
            CacheCandidate(
                "action",
                "bad",
                0.99,
                "2026-07-21T08:00:00+00:00",
                "sf2",
            ),
            {"failed_ids": {"bad"}},
            "failed_quarantine",
        ),
        (
            CacheCandidate(
                "dom",
                "old",
                0.99,
                "2026-07-20T12:00:00+00:00",
                "sf2",
            ),
            {},
            "expired",
        ),
        (
            CacheCandidate(
                "code",
                "new",
                0.99,
                "2026-07-21T11:00:00+00:00",
                "sf2",
            ),
            {"task_started_at": "2026-07-21T10:00:00+00:00"},
            "same_run_write",
        ),
        (
            CacheCandidate(
                "action",
                "v1",
                0.99,
                "2026-07-21T08:00:00+00:00",
                "sf1",
            ),
            {"required_fingerprint_version": "sf2"},
            "fingerprint_version_mismatch",
        ),
    ]
    for candidate, kwargs, reason in cases:
        decision = governance.evaluate(
            candidate,
            threshold=0.9,
            **kwargs,
        )
        assert decision.allowed is False
        assert decision.reason == reason


def test_filter_hits_keeps_only_admissible_candidates_in_backend_order():
    governance = _governance()
    hits = [
        Hit("low", 0.7, "2026-07-21T09:00:00+00:00", "/low"),
        Hit("good", 0.98, "2026-07-21T09:00:00+00:00", "/good"),
        Hit("failed", 0.99, "2026-07-21T09:00:00+00:00", "/failed"),
    ]

    accepted, decisions = governance.filter_hits(
        "code",
        hits,
        threshold=0.9,
        failed_ids={"failed"},
    )

    assert [hit.id for hit in accepted] == ["good"]
    assert [decision.reason for decision in decisions] == [
        "below_threshold",
        "eligible",
        "failed_quarantine",
    ]


def test_missing_timestamp_is_legacy_compatible_but_explicitly_versioned():
    governance = CacheGovernance(
        ttl_hours={"action": 1},
        allow_legacy_fingerprint=True,
        now=lambda: datetime(2026, 7, 21, 12, tzinfo=UTC),
    )
    decision = governance.evaluate(
        CacheCandidate("action", "legacy", 1.0),
        threshold=0.95,
        required_fingerprint_version="sf2",
    )
    assert decision.allowed is True
    assert decision.reason == "eligible"
