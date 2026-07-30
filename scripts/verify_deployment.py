"""Validate a local AutoWeb deployment without calling an LLM or Milvus.

The checker verifies the exact Python environment that will run AutoWeb, the
local dp_cli checkout, and optionally a real headless Chromium startup. It
never prints API keys or attempts a crawl.
"""

from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from dotenv import dotenv_values


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


REQUIRED_MODULES = (
    "DrissionPage",
    "langgraph",
    "pymilvus",
    "dotenv",
)
AUTOWEB_MODULES = (
    "config",
    "core.graph_v2",
    "skills.dpcli_executor",
    "main",
)
RUNTIME_VARIABLES = (
    "BAILIAN_MODEL_NAME",
    "BAILIAN_API_KEY",
    "BAILIAN_BASE_URL",
)


def missing_runtime_variables(env_file: Path) -> list[str]:
    """Return required LLM keys that are absent without exposing values."""
    file_values = dotenv_values(env_file) if env_file.is_file() else {}
    return [
        name
        for name in RUNTIME_VARIABLES
        if not str(os.getenv(name) or file_values.get(name) or "").strip()
    ]


def check_modules(modules: Iterable[str] = REQUIRED_MODULES) -> list[str]:
    """Import runtime dependencies and return diagnostic messages."""
    messages: list[str] = []
    for module in modules:
        importlib.import_module(module)
        messages.append(f"Dependency importable: {module}")
    return messages


def check_autoweb_imports() -> list[str]:
    """Import the application entry and graph without starting an agent run."""
    messages: list[str] = []
    for module in AUTOWEB_MODULES:
        importlib.import_module(module)
        messages.append(f"AutoWeb module importable: {module}")
    return messages


def check_dpcli(dpcli_cwd: Path, timeout: float = 20.0) -> str:
    """Run the local CLI help command in its checkout rather than guessing PATH."""
    entry = dpcli_cwd / "dp_cli" / "__main__.py"
    if not entry.is_file():
        raise RuntimeError(f"dp_cli entry was not found: {entry}")
    completed = subprocess.run(
        [sys.executable, "-m", "dp_cli", "--help"],
        cwd=dpcli_cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()[-800:]
        raise RuntimeError(f"dp_cli --help failed (exit={completed.returncode}): {detail}")
    return f"dp_cli executable: {dpcli_cwd}"


def check_browser(browser_path: str | None = None) -> str:
    """Start and close a real isolated headless Chromium instance."""
    from DrissionPage import Chromium, ChromiumOptions

    options = ChromiumOptions()
    options.headless(True)
    options.auto_port()
    if browser_path:
        path = Path(browser_path).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"Browser path does not exist: {path}")
        options.set_browser_path(str(path))
    browser = None
    try:
        browser = Chromium(options)
        tab = browser.latest_tab
        tab.get("about:blank")
        return "DrissionPage started and closed headless Chromium"
    finally:
        if browser is not None:
            browser.quit()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dpcli-cwd", type=Path)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--require-dpcli", action="store_true")
    parser.add_argument("--require-runtime-config", action="store_true")
    parser.add_argument("--check-browser", action="store_true")
    parser.add_argument("--browser-path")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    messages = [*check_modules(), *check_autoweb_imports()]

    if args.require_runtime_config:
        missing = missing_runtime_variables(args.env_file)
        if missing:
            raise RuntimeError(".env is missing required model configuration: " + ", ".join(missing))
        messages.append("Runtime model configuration present (values hidden)")

    if args.require_dpcli:
        if args.dpcli_cwd is None:
            raise RuntimeError("--require-dpcli also requires --dpcli-cwd")
        messages.append(check_dpcli(args.dpcli_cwd.resolve()))

    if args.check_browser:
        messages.append(check_browser(args.browser_path))

    print("Deployment verification passed")
    for message in messages:
        print(f"  - {message}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Deployment verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
