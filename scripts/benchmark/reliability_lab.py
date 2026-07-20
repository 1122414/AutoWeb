"""Offline deterministic fault-injection lab for production governance."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from skills.cache_governance import CacheCandidate, CacheGovernance
from skills.site_policy import SitePolicy, SitePolicyConfig
from skills.task_lifecycle import TaskLifecycle


def run_lab() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    governance = CacheGovernance(
        ttl_hours={"action": 24},
        allow_legacy_fingerprint=False,
        now=lambda: datetime(2026, 7, 21, 12, tzinfo=UTC),
    )
    cache_scenarios = [
        (
            "expired_cache",
            CacheCandidate(
                "action",
                "old",
                0.99,
                "2026-07-19T00:00:00+00:00",
                "sf2",
            ),
            {},
            "expired",
        ),
        (
            "failed_cache",
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
            "fingerprint_drift",
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
    for name, candidate, kwargs, expected in cache_scenarios:
        decision = governance.evaluate(
            candidate,
            threshold=0.9,
            **kwargs,
        )
        cases.append(
            {
                "name": name,
                "passed": decision.reason == expected,
                "expected": expected,
                "actual": decision.reason,
            }
        )

    lifecycle = TaskLifecycle()
    task = (
        "\u6293\u53d62\u6761\u6807\u9898\u548cURL\uff0c"
        "\u76f4\u5230\u770b\u5230\u201cStop Book\u201d\u3002"
    )
    checkpoint = lifecycle.checkpoint(
        {
            "user_task": task,
            "dpcli_task_contract": lifecycle.compile(task),
            "dpcli_task_progress": {
                "items": [
                    {
                        "title": "Stop Book",
                        "url": "https://example.test/stop",
                    }
                ],
                "completed_pages": [1],
            },
        }
    )
    restored = lifecycle.restore(
        json.loads(json.dumps(checkpoint, ensure_ascii=False))
    )
    cases.append(
        {
            "name": "cold_restart_contract",
            "passed": (
                restored["dpcli_task_contract"]["version"] == 3
                and len(restored["dpcli_task_progress"]["items"]) == 1
            ),
            "expected": "version=3,items=1",
            "actual": (
                f"version={restored['dpcli_task_contract']['version']},"
                f"items={len(restored['dpcli_task_progress']['items'])}"
            ),
        }
    )

    site_policy = SitePolicy(
        SitePolicyConfig(
            robots_enabled=False,
            min_interval_seconds=0,
        )
    )
    private = site_policy.authorize("http://127.0.0.1/admin")
    captcha = site_policy.detect_block_signal(
        {"title": "Verify you are human - CAPTCHA"}
    )
    cases.extend(
        [
            {
                "name": "ssrf_private_network",
                "passed": not private.allowed,
                "expected": "private_network_denied",
                "actual": private.reason,
            },
            {
                "name": "captcha_stop",
                "passed": captcha.detected and captcha.kind == "captcha",
                "expected": "captcha",
                "actual": captcha.kind,
            },
        ]
    )
    passed = sum(bool(case["passed"]) for case in cases)
    return {
        "schema_version": 1,
        "passed": passed,
        "failed": len(cases) - passed,
        "total": len(cases),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="output/benchmark/reliability_lab.json",
    )
    args = parser.parse_args()
    report = run_lab()
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
