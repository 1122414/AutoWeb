"""Run the complex crawler suite across multiple OpenAI-compatible models."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODELS = ("deepseek-v4-flash", "kimi-k2.6", "kimi-k2.5")
OFFICIAL_CNY_PRICING = {
    "deepseek-v4-flash": {"input": 1.0, "output": 2.0},
    "kimi-k2.6": {"input": 6.5, "output": 27.0},
    "kimi-k2.5": {"input": 4.0, "output": 21.0},
}
PRICING_SOURCE_URLS = {
    "deepseek-v4-flash": "https://help.aliyun.com/zh/model-studio/deepseek-v4-flash",
    "kimi-k2.6": "https://help.aliyun.com/zh/model-studio/kimi-k2-6",
    "kimi-k2.5": "https://help.aliyun.com/zh/model-studio/kimi-k2-5",
}
DEFAULT_CASES = (
    "products_three_pages",
    "quotes_infinite_scroll",
    "books_list_detail",
    "hockey_filter_two_pages",
    "products_restart_resume",
)
MODEL_ENV_KEYS = (
    "BAILIAN_MODEL_NAME",
    "CODER_MODEL_NAME",
    "OBSERVER_MODEL_NAME",
    "PLANNER_MODEL_NAME",
    "VERIFIER_MODEL_NAME",
    "SUMMARIZER_MODEL_NAME",
)


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-") or "model"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _task_pass(run: dict[str, Any]) -> bool:
    checks = (run.get("evaluation") or {}).get("checks") or {}
    return bool(checks) and all(bool(value) for value in checks.values())


def _estimated_cost_cny(model: str, usage: dict[str, Any]) -> float:
    pricing = OFFICIAL_CNY_PRICING.get(model) or {}
    return round(
        (
            int(usage.get("input_tokens") or 0) * float(pricing.get("input") or 0)
            + int(usage.get("output_tokens") or 0) * float(pricing.get("output") or 0)
        )
        / 1_000_000,
        8,
    )


def _matrix_summary(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    models = list(dict.fromkeys(str(run.get("model") or "") for run in runs))
    for model in models:
        selected = [run for run in runs if run.get("model") == model]
        count = len(selected)
        usage = [run.get("usage") or {} for run in selected]
        rows.append(
            {
                "model": model,
                "run_count": count,
                "passed": sum(_task_pass(run) for run in selected),
                "success_rate": round(
                    sum(_task_pass(run) for run in selected) / count * 100, 1
                ) if count else 0.0,
                "average_accuracy": round(
                    sum(float((run.get("evaluation") or {}).get("accuracy_score") or 0) for run in selected)
                    / count,
                    1,
                ) if count else 0.0,
                "average_elapsed_seconds": round(
                    sum(float(run.get("elapsed_seconds") or 0) for run in selected) / count,
                    3,
                ) if count else 0.0,
                "total_tokens": sum(int(item.get("total_tokens") or 0) for item in usage),
                "average_tokens": round(
                    sum(int(item.get("total_tokens") or 0) for item in usage) / count,
                    1,
                ) if count else 0.0,
                "input_tokens": sum(int(item.get("input_tokens") or 0) for item in usage),
                "output_tokens": sum(int(item.get("output_tokens") or 0) for item in usage),
                "llm_call_count": sum(int(item.get("llm_call_count") or 0) for item in usage),
                "browser_action_count": sum(
                    int(item.get("browser_action_count") or 0) for item in usage
                ),
                "average_browser_actions": round(
                    sum(int(item.get("browser_action_count") or 0) for item in usage) / count,
                    1,
                ) if count else 0.0,
                "total_cost_usd": round(
                    sum(float(item.get("cost_usd") or 0) for item in usage), 8
                ),
                "average_cost_usd": round(
                    sum(float(item.get("cost_usd") or 0) for item in usage) / count,
                    8,
                ) if count else 0.0,
                "total_cost_cny": round(
                    sum(float(run.get("estimated_cost_cny") or 0) for run in selected),
                    8,
                ),
                "average_cost_cny": round(
                    sum(float(run.get("estimated_cost_cny") or 0) for run in selected)
                    / count,
                    8,
                ) if count else 0.0,
                "average_resume_count": round(
                    sum(int(run.get("resume_count") or 0) for run in selected) / count,
                    1,
                ) if count else 0.0,
                "skill_selection_decisions": sum(
                    int((run.get("skill_selection") or {}).get("decision_count") or 0)
                    for run in selected
                ),
                "selected_skills": list(dict.fromkeys(
                    str(name)
                    for run in selected
                    for name in ((run.get("skill_selection") or {}).get("selected_skills") or [])
                    if name
                )),
                "llm_duration_seconds": round(
                    sum(float(item.get("llm_duration_ms") or 0) for item in usage) / 1000,
                    3,
                ),
                "estimated_call_count": sum(
                    int(item.get("estimated_call_count") or 0) for item in usage
                ),
            }
        )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--cases", default=",".join(DEFAULT_CASES))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-resumes", type=int, default=30)
    parser.add_argument("--suite-timeout", type=int, default=3600)
    parser.add_argument(
        "--runner",
        default="benchmark_complex_tasks.py",
        choices=("benchmark_complex_tasks.py", "benchmark_cross_site_tasks.py"),
        help="Benchmark runner script located under scripts/benchmark.",
    )
    parser.add_argument(
        "--output",
        default=str(
            PROJECT_ROOT
            / "output"
            / "benchmarks"
            / f"model_matrix_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        ),
    )
    parser.add_argument(
        "--report",
        default=str(PROJECT_ROOT / "output" / "reports" / "autoweb_model_matrix.html"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    models = [value.strip() for value in args.models.split(",") if value.strip()]
    cases = [value.strip() for value in args.cases.split(",") if value.strip()]
    if not models or not cases:
        raise SystemExit("--models and --cases must not be empty")
    if args.repeats < 1 or args.max_resumes < 1:
        raise SystemExit("--repeats and --max-resumes must be at least 1")

    output_path = Path(args.output).resolve()
    artifact_dir = output_path.parent / f"{output_path.stem}_artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(),
        "configuration": {
            "models": models,
            "cases": cases,
            "repeats": args.repeats,
            "max_resumes": args.max_resumes,
                "enable_thinking": False,
            "task_contract_planner": False,
            "skill_selection_mode": "llm_metadata_then_progressive_body_load",
            "cache_isolation": "all reusable caches disabled during model comparison",
            "provider": "Alibaba Cloud Model Studio OpenAI-compatible endpoint",
            "pricing_currency": "CNY",
            "pricing_cny_per_million_tokens": OFFICIAL_CNY_PRICING,
            "pricing_sources": PRICING_SOURCE_URLS,
            "runner": args.runner,
        },
        "suites": [],
        "runs": [],
        "summary": [],
    }
    matrix_started = time.monotonic()

    for model in models:
        slug = _slug(model)
        artifact = artifact_dir / f"{slug}.json"
        stdout_log = artifact_dir / f"{slug}.stdout.log"
        stderr_log = artifact_dir / f"{slug}.stderr.log"
        trace_db = artifact_dir / f"{slug}.trace.sqlite3"
        env = os.environ.copy()
        for key in MODEL_ENV_KEYS:
            env[key] = model
        env.update(
            {
                "LLM_ENABLE_THINKING": "false",
                "CODE_CACHE_ENABLED": "false",
                "DOM_CACHE_ENABLED": "false",
                "ACTION_CACHE_ENABLED": "false",
                "RUN_TRACE_ENABLED": "true",
                "RUN_TRACE_DB_PATH": str(trace_db),
                "TASK_RUN_PERSISTENCE_ENABLED": "false",
                "DPCLI_ENABLED": "true",
                "DPCLI_TASK_CONTRACT_ENABLED": "false",
                "AGENT_SKILLS_ENABLED": "true",
                "DPCLI_HEADLESS": "true",
                "HEADLESS_MODE": "true",
                "PYTHONIOENCODING": "utf-8",
                "LANGCHAIN_TRACING": "false",
                "LANGCHAIN_TRACING_V2": "false",
                "LANGSMITH_TRACING": "false",
            }
        )
        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "benchmark" / args.runner),
            "--cases",
            ",".join(cases),
            "--repeats",
            str(args.repeats),
            "--max-resumes",
            str(args.max_resumes),
            "--output",
            str(artifact),
        ]
        print(f"\n=== model matrix: {model} ===", flush=True)
        suite_started = time.monotonic()
        suite: dict[str, Any] = {
            "model": model,
            "artifact": str(artifact),
            "stdout_log": str(stdout_log),
            "stderr_log": str(stderr_log),
            "trace_db": str(trace_db),
        }
        try:
            completed = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=args.suite_timeout,
                check=False,
            )
            stdout_log.write_text(completed.stdout, encoding="utf-8")
            stderr_log.write_text(completed.stderr, encoding="utf-8")
            suite.update(
                {
                    "status": "completed" if completed.returncode == 0 else "failed",
                    "returncode": completed.returncode,
                    "elapsed_seconds": round(time.monotonic() - suite_started, 3),
                }
            )
        except subprocess.TimeoutExpired as exc:
            stdout_log.write_text(exc.stdout or "", encoding="utf-8")
            stderr_log.write_text(exc.stderr or "", encoding="utf-8")
            suite.update(
                {
                    "status": "timeout",
                    "returncode": None,
                    "elapsed_seconds": round(time.monotonic() - suite_started, 3),
                    "error": f"suite exceeded {args.suite_timeout} seconds",
                }
            )
        except Exception as exc:
            suite.update(
                {
                    "status": "exception",
                    "returncode": None,
                    "elapsed_seconds": round(time.monotonic() - suite_started, 3),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

        if artifact.exists():
            try:
                suite_payload = json.loads(artifact.read_text(encoding="utf-8"))
                for run in suite_payload.get("runs") or []:
                    enriched = dict(run)
                    enriched["model"] = model
                    enriched["source_artifact"] = str(artifact)
                    enriched["estimated_cost_cny"] = _estimated_cost_cny(
                        model, enriched.get("usage") or {}
                    )
                    payload["runs"].append(enriched)
                suite["run_count"] = len(suite_payload.get("runs") or [])
            except Exception as exc:
                suite["artifact_error"] = f"{type(exc).__name__}: {exc}"
        payload["suites"].append(suite)
        payload["summary"] = _matrix_summary(payload["runs"])
        payload["matrix_elapsed_seconds"] = round(time.monotonic() - matrix_started, 3)
        _write_json(output_path, payload)
        print(json.dumps(suite, ensure_ascii=False), flush=True)

    payload["generated_at"] = datetime.now().isoformat()
    payload["summary"] = _matrix_summary(payload["runs"])
    payload["matrix_elapsed_seconds"] = round(time.monotonic() - matrix_started, 3)
    _write_json(output_path, payload)

    if args.report:
        try:
            from generate_model_comparison_report import generate_report
        except ImportError:
            from scripts.benchmark.generate_model_comparison_report import generate_report
        report_path = generate_report(output_path, Path(args.report).resolve())
        print(f"Combined HTML report: {report_path}", flush=True)
    print(f"Model matrix result: {output_path}", flush=True)
    return 0 if all(item.get("status") == "completed" for item in payload["suites"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
