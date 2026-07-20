from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import Mock, patch

from skills.dpcli_executor import DPCLIExecutor
from skills.site_policy import BlockingSignal


@dataclass
class _Decision:
    allowed: bool
    reason: str
    url: str = "https://example.test"

    def to_dict(self):
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "url": self.url,
        }


class _Policy:
    def __init__(self, decision, signal=None):
        self.decision = decision
        self.signal = signal or BlockingSignal(False)

    def authorize_action(self, _action):
        return [self.decision]

    def detect_block_signal(self, _payload):
        return self.signal


def _executor(policy):
    return DPCLIExecutor(
        session="policy-test",
        headless=True,
        python_executable="python",
        cwd=".",
        site_policy=policy,
    )


def test_denied_open_never_launches_cli_process():
    policy = _Policy(_Decision(False, "robots_denied"))
    with patch("skills.dpcli_executor.subprocess.run") as run:
        result = _executor(policy).execute_action(
            {
                "skill": "open",
                "params": {"url": "https://example.test/private"},
            }
        )

    assert result["ok"] is False
    assert result["error"]["code"] == "site_policy_denied"
    run.assert_not_called()


def test_allowed_open_persists_policy_evidence():
    policy = _Policy(_Decision(True, "allowed"))
    payload = {
        "ok": True,
        "session": "policy-test",
        "action": "open",
        "data": {"page": {}},
        "error": None,
    }
    with patch("skills.dpcli_executor.subprocess.run") as run:
        run.return_value = Mock(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )
        result = _executor(policy).execute_action(
            {
                "skill": "open",
                "params": {"url": "https://example.test"},
            }
        )

    assert result["ok"] is True
    assert result["_site_policy"]["decisions"][0]["reason"] == "allowed"


def test_captcha_signal_converts_success_into_terminal_policy_error():
    policy = _Policy(
        _Decision(True, "allowed"),
        BlockingSignal(True, "captcha", "captcha"),
    )
    payload = {
        "ok": True,
        "session": "policy-test",
        "action": "open",
        "data": {"page": {"title": "CAPTCHA"}},
        "error": None,
    }
    with patch("skills.dpcli_executor.subprocess.run") as run:
        run.return_value = Mock(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )
        result = _executor(policy).execute_action(
            {
                "skill": "open",
                "params": {"url": "https://example.test"},
            }
        )

    assert result["ok"] is False
    assert result["error"]["code"] == "site_blocked"
    assert result["_site_policy"]["blocking_signal"]["kind"] == "captcha"
