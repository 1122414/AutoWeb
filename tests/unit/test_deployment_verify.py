from __future__ import annotations

from scripts.verify_deployment import missing_runtime_variables


def test_missing_runtime_variables_does_not_require_or_expose_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "BAILIAN_MODEL_NAME=test-model\nBAILIAN_API_KEY=secret\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("BAILIAN_MODEL_NAME", raising=False)
    monkeypatch.delenv("BAILIAN_API_KEY", raising=False)
    monkeypatch.delenv("BAILIAN_BASE_URL", raising=False)

    assert missing_runtime_variables(env_file) == ["BAILIAN_BASE_URL"]


def test_environment_variables_override_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("BAILIAN_MODEL_NAME=from-file\n", encoding="utf-8")
    monkeypatch.setenv("BAILIAN_API_KEY", "from-environment")
    monkeypatch.setenv("BAILIAN_BASE_URL", "https://example.test/v1")

    assert missing_runtime_variables(env_file) == []
