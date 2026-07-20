from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Benchmarks must be unattended and must not open visible browser windows.
os.environ["HEADLESS_MODE"] = "true"
os.environ["DPCLI_HEADLESS"] = "true"
os.environ["LANGCHAIN_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"

from config import (
    CODER_API_KEY,
    CODER_BASE_URL,
    CODER_MODEL_NAME,
    MODEL_NAME,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    PLANNER_API_KEY,
    PLANNER_BASE_URL,
    PLANNER_MODEL_NAME,
    VERIFIER_API_KEY,
    VERIFIER_BASE_URL,
    VERIFIER_MODEL_NAME,
)
from core.graph_v2 import build_graph
from core.llm_factory import create_llm
from langgraph.checkpoint.memory import MemorySaver
from skills.observer import BrowserObserver
from skills.dpcli_executor import DPCLIExecutor


@dataclass(frozen=True)
class BenchmarkCase:
    key: str
    name: str
    url: str
    task: str
    expected_min_items: int
    expected_max_items: int
    required_field_groups: tuple[tuple[str, ...], ...]
    anchor_values: tuple[str, ...]
    capability: str
    allow_detail_batch: bool = False
    anchor_required: bool = True
    minimum_chinese_title_ratio: float = 0.0
    relevant_url_patterns: tuple[str, ...] = ()
    forbidden_url_patterns: tuple[str, ...] = ()
    minimum_relevant_item_ratio: float = 0.0
    minimum_title_length: int = 0
    restart_after_pages: int = 0


CASES = {
    "books_static": BenchmarkCase(
        key="books_static",
        name="Books to Scrape - static product list",
        url="https://books.toscrape.com/",
        task=(
            "打开 https://books.toscrape.com/，只提取当前第一页前5本书的标题和对应URL，"
            "得到5条有效数据后立即结束任务。"
        ),
        expected_min_items=5,
        expected_max_items=5,
        required_field_groups=(("title", "name"), ("url", "href", "detail_url")),
        anchor_values=("a light in the", "tipping the velvet"),
        capability="静态列表、语义区域识别、结构化链接提取",
    ),
    "quotes_static": BenchmarkCase(
        key="quotes_static",
        name="Quotes to Scrape - static quotes",
        url="https://quotes.toscrape.com/",
        task=(
            "打开 https://quotes.toscrape.com/，提取当前第一页10条名言的正文、作者和标签。"
            "得到10条数据后结束任务。"
        ),
        expected_min_items=10,
        expected_max_items=10,
        required_field_groups=(
            ("text", "quote", "content", "title"),
            ("author", "name"),
            ("tags", "tag"),
        ),
        anchor_values=("albert einstein", "world as we have created"),
        capability="重复内容块、嵌套文本、作者与标签字段",
    ),
    "quotes_js": BenchmarkCase(
        key="quotes_js",
        name="Quotes to Scrape - JavaScript rendering",
        url="https://quotes.toscrape.com/js/",
        task=(
            "打开 https://quotes.toscrape.com/js/，等待 JavaScript 内容加载，"
            "提取当前第一页10条名言的正文和作者，得到10条数据后结束任务。"
        ),
        expected_min_items=10,
        expected_max_items=10,
        required_field_groups=(
            ("text", "quote", "content", "title"),
            ("author", "name"),
        ),
        anchor_values=("albert einstein", "j.k. rowling"),
        capability="JavaScript 渲染、等待与动态 DOM 观察",
    ),
    "products_pagination": BenchmarkCase(
        key="products_pagination",
        name="web-scraping.dev - product pagination",
        url="https://web-scraping.dev/products",
        task=(
            "打开 https://web-scraping.dev/products，提取当前第一页5个商品的名称、价格和对应URL，"
            "然后进入第2页再提取5个商品；合计至少10条数据后结束任务。"
        ),
        expected_min_items=10,
        expected_max_items=10,
        required_field_groups=(
            ("title", "name"),
            ("price",),
            ("url", "href", "detail_url"),
        ),
        anchor_values=("box of chocolate candy", "dark red energy potion"),
        capability="静态分页、商品字段、跨页累计",
    ),
    "hockey_table": BenchmarkCase(
        key="hockey_table",
        name="Scrape This Site - hockey table",
        url="https://www.scrapethissite.com/pages/forms/",
        task=(
            "打开 https://www.scrapethissite.com/pages/forms/，提取当前第一页25行球队数据，"
            "字段包括球队名称、年份、胜场和负场；得到25行后结束任务。"
        ),
        expected_min_items=25,
        expected_max_items=25,
        required_field_groups=(
            ("team", "team_name", "name", "title"),
            ("year",),
            ("wins", "win"),
            ("losses", "loss"),
        ),
        anchor_values=("boston bruins", "buffalo sabres"),
        capability="HTML 表格、数字字段、25行批量抽取",
    ),
}


class _PlaceholderTab:
    url = "about:blank"


class _PlaceholderBrowser:
    """The dp_cli path owns its browser; graph helpers only need a current URL."""

    latest_tab = _PlaceholderTab()


def setup_benchmark_agent() -> tuple[Any, Any]:
    default_llm = create_llm(MODEL_NAME, OPENAI_API_KEY, OPENAI_BASE_URL)
    coder_llm = create_llm(CODER_MODEL_NAME, CODER_API_KEY, CODER_BASE_URL)
    planner_llm = create_llm(
        PLANNER_MODEL_NAME,
        PLANNER_API_KEY,
        PLANNER_BASE_URL,
    )
    verifier_llm = create_llm(
        VERIFIER_MODEL_NAME,
        VERIFIER_API_KEY,
        VERIFIER_BASE_URL,
    )
    app = build_graph(
        checkpointer=MemorySaver(),
        llm=default_llm,
        observer=BrowserObserver(),
        coder_llm=coder_llm,
        planner_llm=planner_llm,
        verifier_llm=verifier_llm,
    )
    return app, _PlaceholderBrowser()


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return str(value)


def _page_url(result: dict[str, Any]) -> str:
    data = result.get("data") if isinstance(result, dict) else {}
    data = data if isinstance(data, dict) else {}
    page = data.get("page")
    if isinstance(page, dict) and page.get("url"):
        return str(page["url"])
    identity = data.get("page_identity")
    if isinstance(identity, dict) and identity.get("url"):
        return str(identity["url"])
    return ""


def _result_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(result, dict) or not result.get("ok"):
        return []
    data = result.get("data")
    if not isinstance(data, dict):
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        merged: dict[str, Any] = {}
        if isinstance(item.get("list_info"), dict):
            merged.update(item["list_info"])
        if isinstance(item.get("detail_info"), dict):
            merged.update(item["detail_info"])
        merged.update(item)
        normalized.append(merged)
    return normalized


def _meaningful(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _title_value(item: dict[str, Any]) -> str:
    normalized = {str(key).lower(): value for key, value in item.items()}
    for alias in ("title", "name", "text", "content"):
        value = normalized.get(alias)
        if _meaningful(value):
            return str(value)
    return ""


def _item_url(item: dict[str, Any]) -> str:
    return str(
        item.get("final_url")
        or item.get("detail_url")
        or item.get("url")
        or item.get("href")
        or ""
    ).strip()


def _is_relevant_item(case: BenchmarkCase, item: dict[str, Any]) -> bool:
    title = _title_value(item)
    url = _item_url(item)
    if case.minimum_title_length and len(title) < case.minimum_title_length:
        return False
    if case.relevant_url_patterns and not any(
        re.search(pattern, url, flags=re.IGNORECASE)
        for pattern in case.relevant_url_patterns
    ):
        return False
    if any(
        re.search(pattern, url, flags=re.IGNORECASE)
        for pattern in case.forbidden_url_patterns
    ):
        return False
    return bool(title and url)


def _evaluate(
    case: BenchmarkCase,
    status: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    target_domain = urlparse(case.url).netloc
    opened = any(
        item.get("ok")
        and target_domain
        and target_domain in urlparse(_page_url(item)).netloc
        for item in results
    )

    data_results = [
        result
        for result in results
        if str(result.get("action") or "").lower()
        in {"extract", "list-items", "batch-detail-extract"}
    ]
    all_items: list[dict[str, Any]] = []
    for result in data_results:
        all_items.extend(_result_items(result))

    unique_items: list[dict[str, Any]] = []
    by_identity: dict[str, int] = {}
    for item in all_items:
        identity = str(
            item.get("final_url")
            or item.get("detail_url")
            or item.get("url")
            or item.get("href")
            or json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        ).strip().rstrip("/")
        if not identity:
            continue
        existing_index = by_identity.get(identity)
        if existing_index is None:
            by_identity[identity] = len(unique_items)
            unique_items.append(dict(item))
            continue
        existing = unique_items[existing_index]
        for key, value in item.items():
            if _meaningful(value):
                existing[key] = value

    field_group_coverage: dict[str, float] = {}
    for aliases in case.required_field_groups:
        populated = 0
        for item in unique_items:
            normalized = {str(key).lower(): value for key, value in item.items()}
            if any(_meaningful(normalized.get(alias.lower())) for alias in aliases):
                populated += 1
        field_group_coverage["|".join(aliases)] = (
            populated / len(unique_items) if unique_items else 0.0
        )

    corpus = json.dumps(unique_items, ensure_ascii=False, default=str).lower()
    anchor_match = any(anchor.lower() in corpus for anchor in case.anchor_values)
    if not case.anchor_values and not case.anchor_required:
        anchor_match = True
    chinese_title_count = sum(
        bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", _title_value(item)))
        for item in unique_items
    )
    chinese_title_ratio = (
        chinese_title_count / len(unique_items) if unique_items else 0.0
    )
    relevant_item_count = sum(
        _is_relevant_item(case, item) for item in unique_items
    )
    relevant_item_ratio = (
        relevant_item_count / len(unique_items) if unique_items else 0.0
    )
    field_pass = bool(field_group_coverage) and all(
        coverage >= 0.8 for coverage in field_group_coverage.values()
    )
    detail_batch_ran = any(
        str(result.get("action") or "").lower() == "batch-detail-extract"
        for result in results
    )
    checks = {
        "target_opened": opened,
        "minimum_unique_items": len(unique_items) >= case.expected_min_items,
        "maximum_unique_items": len(unique_items) <= case.expected_max_items,
        "required_field_coverage_80pct": field_pass,
        "known_anchor_present": anchor_match,
        "no_unrequested_detail_batch": (
            case.allow_detail_batch or not detail_batch_ran
        ),
        "autonomous_completion": status == "completed",
    }
    if case.minimum_chinese_title_ratio > 0:
        checks["chinese_title_ratio"] = (
            chinese_title_ratio >= case.minimum_chinese_title_ratio
        )
    if case.minimum_relevant_item_ratio > 0:
        checks["content_relevance"] = (
            relevant_item_ratio >= case.minimum_relevant_item_ratio
        )
    passed = sum(1 for value in checks.values() if value)
    return {
        "checks": checks,
        "accuracy_score": round(passed / len(checks) * 100, 1),
        "unique_item_count": len(unique_items),
        "field_group_coverage": field_group_coverage,
        "anchor_match": anchor_match,
        "chinese_title_ratio": round(chinese_title_ratio, 3),
        "relevant_item_ratio": round(relevant_item_ratio, 3),
        "detail_batch_ran": detail_batch_ran,
        "item_sample": unique_items[:3],
    }


def _compact_update(node: str, update: Any) -> dict[str, Any]:
    payload = update if isinstance(update, dict) else {}
    compact: dict[str, Any] = {"node": node}
    for key in (
        "current_url",
        "plan",
        "generated_action",
        "execution_mode",
        "execution_log",
        "verification_result",
        "dpcli_result",
        "dpcli_structured_plan",
        "dpcli_target_result",
        "dpcli_task_contract",
        "dpcli_task_progress",
        "error",
        "error_type",
        "is_complete",
        "loop_count",
    ):
        if key in payload:
            value = payload[key]
            if key == "execution_log" and isinstance(value, str):
                value = value[:1000]
            if key == "dpcli_result" and isinstance(value, dict):
                data = value.get("data") if isinstance(value.get("data"), dict) else {}
                value = {
                    "ok": value.get("ok"),
                    "action": value.get("action"),
                    "error": value.get("error"),
                    "page_url": _page_url(value),
                    "item_count": data.get("item_count"),
                    "detail_pages_extracted": data.get("detail_pages_extracted"),
                    "projection": data.get("projection"),
                    "items": _result_items(value)[:5],
                }
            if key == "dpcli_task_progress" and isinstance(value, dict):
                value = {
                    "item_count": len(value.get("items") or []),
                    "completed_pages": value.get("completed_pages") or [],
                    "active_page": value.get("active_page"),
                    "failed_region_refs": value.get("failed_region_refs") or [],
                    "filter_applied": value.get("filter_applied"),
                    "scroll_round": value.get("scroll_round"),
                    "stagnant_rounds": value.get("stagnant_rounds"),
                    "list_complete": value.get("list_complete"),
                    "detail_complete": value.get("detail_complete"),
                }
            compact[key] = _json_safe(value)
    return compact


def _terminal_status(values: dict[str, Any]) -> str:
    if not values.get("is_complete"):
        return "stopped"
    verification = values.get("verification_result") or {}
    return "completed" if verification.get("is_success") is True else "failed"


def run_case(
    app: Any,
    browser: Any,
    case: BenchmarkCase,
    repeat: int,
    max_resumes: int,
) -> dict[str, Any]:
    session = f"benchmark-{case.key}-{repeat}-{uuid.uuid4().hex[:8]}"
    config = {
        "configurable": {
            "thread_id": str(uuid.uuid4()),
            "browser": browser,
        },
        "recursion_limit": 50,
    }
    initial_state = {
        "user_task": case.task,
        "messages": [("user", case.task)],
        "loop_count": 0,
        "finished_steps": [],
        "reflections": [],
        "hitl_mode": "off",
        "_task_started_at": datetime.now().isoformat(),
        "_cache_failed_this_round": False,
        "_cache_hit_id": None,
        "_failed_code_cache_ids": [],
        "generated_action": None,
        "execution_mode": "dp_cli",
        "dpcli_session": session,
        "dpcli_result": None,
        "dpcli_snapshot": None,
        "dpcli_snapshot_view": None,
        "dpcli_task_contract": None,
        "dpcli_task_progress": None,
        "dpcli_detail_batch_ran": False,
        "_action_source": None,
        "_action_cache_hit_id": None,
        "_failed_action_cache_ids": [],
        "_dpcli_action_disabled": False,
        "_failed_dom_cache_ids": [],
        "_step_fail_count": 0,
        "_error_recovery_count": 0,
        "_last_recovery_error": None,
        "coder_retry_count": 0,
    }

    started = time.monotonic()
    events: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    exception = None
    status = "max_resumes"
    next_input: Any = initial_state
    resume_count = 0
    restart_count = 0
    restart_checkpoint: dict[str, Any] | None = None

    try:
        for resume_count in range(max_resumes + 1):
            for event in app.stream(next_input, config=config, stream_mode="updates"):
                if not isinstance(event, dict):
                    continue
                for node, update in event.items():
                    events.append(_compact_update(str(node), update))
                    if isinstance(update, dict):
                        result = update.get("dpcli_result")
                        if isinstance(result, dict):
                            results.append(_json_safe(result))

            snapshot = app.get_state(config)
            next_nodes = tuple(getattr(snapshot, "next", ()) or ())
            values = getattr(snapshot, "values", {}) or {}
            progress = values.get("dpcli_task_progress") or {}
            completed_pages = progress.get("completed_pages") or []
            if (
                case.restart_after_pages > 0
                and restart_count == 0
                and len(completed_pages) >= case.restart_after_pages
                and not values.get("is_complete")
            ):
                checkpoint_payload = _json_safe(
                    {
                        "dpcli_task_contract": values.get("dpcli_task_contract"),
                        "dpcli_task_progress": progress,
                    }
                )
                recovered_state = dict(values)
                recovered_state.update(checkpoint_payload)
                recovered_state.update(
                    {
                        "generated_action": None,
                        "generated_code": None,
                        "dpcli_result": None,
        "dpcli_snapshot": None,
        "dpcli_snapshot_view": None,
        "dpcli_snapshot_delta": None,
        "dpcli_snapshot_ref": None,
                        "dpcli_agent_view": None,
                        "dpcli_snapshot_index": None,
                        "dpcli_structured_plan": None,
                        "dpcli_target_result": None,
                        "verification_result": None,
                        "is_complete": False,
                    }
                )
                config = {
                    "configurable": {
                        "thread_id": str(uuid.uuid4()),
                        "browser": browser,
                    },
                    "recursion_limit": 50,
                }
                restart_count = 1
                restart_checkpoint = {
                    "completed_pages": list(completed_pages),
                    "item_count": len(progress.get("items") or []),
                    "active_page": progress.get("active_page"),
                }
                events.append(
                    {
                        "node": "__simulated_restart__",
                        "restart_checkpoint": restart_checkpoint,
                    }
                )
                next_input = recovered_state
                continue
            if not next_nodes:
                status = _terminal_status(values)
                break
            next_input = None
    except Exception as exc:
        status = "exception"
        exception = f"{type(exc).__name__}: {exc}"
    finally:
        close_result = DPCLIExecutor(session=session, headless=True).session_close()

    elapsed = time.monotonic() - started
    evaluation = _evaluate(case, status, results)
    return {
        "case": asdict(case),
        "repeat": repeat,
        "session": session,
        "status": status,
        "exception": exception,
        "elapsed_seconds": round(elapsed, 3),
        "resume_count": resume_count,
        "event_count": len(events),
        "restart_simulated": bool(restart_count),
        "restart_count": restart_count,
        "restart_checkpoint": restart_checkpoint,
        "events": events,
        "results": [
            {
                "ok": result.get("ok"),
                "action": result.get("action"),
                "error": result.get("error"),
                "page_url": _page_url(result),
                "projection": (
                    (result.get("data") or {}).get("projection")
                    if isinstance(result.get("data"), dict)
                    else None
                ),
                "items": _result_items(result)[:10],
            }
            for result in results
        ],
        "evaluation": evaluation,
        "session_close": _json_safe(close_result),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run repeatable natural-language AutoWeb benchmarks."
    )
    parser.add_argument(
        "--cases",
        default=",".join(CASES),
        help=f"Comma-separated case keys. Available: {', '.join(CASES)}",
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-resumes", type=int, default=12)
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected_keys = [item.strip() for item in args.cases.split(",") if item.strip()]
    unknown = [item for item in selected_keys if item not in CASES]
    if unknown:
        raise SystemExit(f"Unknown cases: {', '.join(unknown)}")
    if args.repeats < 1:
        raise SystemExit("--repeats must be at least 1")
    if args.max_resumes < 1:
        raise SystemExit("--max-resumes must be at least 1")

    output_path = (
        Path(args.output)
        if args.output
        else PROJECT_ROOT
        / "output"
        / "benchmarks"
        / f"natural_language_benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    app = browser = None
    runs: list[dict[str, Any]] = []
    suite_started = time.monotonic()
    app, browser = setup_benchmark_agent()
    for repeat in range(1, args.repeats + 1):
        for key in selected_keys:
            print(f"\n=== benchmark {key} repeat={repeat} ===", flush=True)
            run = run_case(
                app=app,
                browser=browser,
                case=CASES[key],
                repeat=repeat,
                max_resumes=args.max_resumes,
            )
            runs.append(run)
            output_path.write_text(
                json.dumps(
                    {
                        "generated_at": datetime.now().isoformat(),
                        "suite_elapsed_seconds": round(
                            time.monotonic() - suite_started, 3
                        ),
                        "runs": runs,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(
                json.dumps(
                    {
                        "case": key,
                        "status": run["status"],
                        "accuracy_score": run["evaluation"]["accuracy_score"],
                        "unique_item_count": run["evaluation"][
                            "unique_item_count"
                        ],
                        "elapsed_seconds": run["elapsed_seconds"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    payload = {
        "generated_at": datetime.now().isoformat(),
        "suite_elapsed_seconds": round(time.monotonic() - suite_started, 3),
        "configuration": {
            "headless": True,
            "max_resumes": args.max_resumes,
            "repeats": args.repeats,
            "cases": selected_keys,
        },
        "runs": runs,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nBenchmark result: {output_path.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
