"""Unified Run Trace storage for model usage, browser actions, and cost."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


TRACE_SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _message_text(messages: Any) -> str:
    if isinstance(messages, str):
        return messages
    if isinstance(messages, Mapping):
        return json.dumps(messages, ensure_ascii=False, default=str)
    if isinstance(messages, Iterable):
        parts = []
        for message in messages:
            content = getattr(message, "content", message)
            parts.append(str(content))
        return "\n".join(parts)
    return str(messages or "")


def _count_tokens(text: str) -> int:
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(str(text or "")))
    except Exception:
        return max(0, len(str(text or "")) // 2)


def _usage_from_response(
    response: Any,
    input_text: str,
) -> tuple[int, int, int, bool]:
    usage = getattr(response, "usage_metadata", None) or {}
    metadata = getattr(response, "response_metadata", None) or {}
    if not isinstance(usage, Mapping):
        usage = {}
    if not isinstance(metadata, Mapping):
        metadata = {}
    token_usage = metadata.get("token_usage") or metadata.get("usage") or {}
    if not isinstance(token_usage, Mapping):
        token_usage = {}
    input_tokens = int(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or token_usage.get("input_tokens")
        or token_usage.get("prompt_tokens")
        or 0
    )
    output_tokens = int(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or token_usage.get("output_tokens")
        or token_usage.get("completion_tokens")
        or 0
    )
    total_tokens = int(
        usage.get("total_tokens")
        or token_usage.get("total_tokens")
        or input_tokens + output_tokens
    )
    estimated = not bool(input_tokens or output_tokens or total_tokens)
    if estimated:
        input_tokens = _count_tokens(input_text)
        output_tokens = _count_tokens(str(getattr(response, "content", "") or ""))
        total_tokens = input_tokens + output_tokens
    return input_tokens, output_tokens, total_tokens, estimated


def _model_name(llm: Any) -> str:
    for attribute in ("model_name", "model"):
        value = getattr(llm, attribute, None)
        if value:
            return str(value)
    return type(llm).__name__


@dataclass(frozen=True)
class TraceEvent:
    thread_id: str
    event_type: str
    node: str
    model: str
    started_at: str
    duration_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_tokens: bool = False
    cost_usd: float = 0.0
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class RunUsageSummary:
    thread_id: str
    event_count: int
    llm_call_count: int
    browser_action_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_call_count: int
    cost_usd: float
    total_duration_ms: float


class RunTraceStore:
    """Persist and aggregate append-only Task Run evidence."""

    def __init__(
        self,
        path: str | Path,
        *,
        pricing: Mapping[str, Mapping[str, float]] | None = None,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.pricing = {
            str(model): {
                "input_per_million": float(values.get("input_per_million", 0)),
                "output_per_million": float(values.get("output_per_million", 0)),
            }
            for model, values in (pricing or {}).items()
            if isinstance(values, Mapping)
        }
        self._lock = threading.RLock()
        self._setup()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _setup(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS autoweb_run_trace (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schema_version INTEGER NOT NULL,
                    thread_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    node TEXT NOT NULL,
                    model TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    duration_ms REAL NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    estimated_tokens INTEGER NOT NULL,
                    cost_usd REAL NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_autoweb_trace_thread
                ON autoweb_run_trace(thread_id, id)
                """
            )

    def calculate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        price = self.pricing.get(model) or {}
        return round(
            (
                input_tokens * float(price.get("input_per_million", 0))
                + output_tokens * float(price.get("output_per_million", 0))
            )
            / 1_000_000,
            8,
        )

    def append(self, event: TraceEvent) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO autoweb_run_trace (
                    schema_version, thread_id, event_type, node, model,
                    started_at, duration_ms, input_tokens, output_tokens,
                    total_tokens, estimated_tokens, cost_usd, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    TRACE_SCHEMA_VERSION,
                    event.thread_id,
                    event.event_type,
                    event.node,
                    event.model,
                    event.started_at,
                    float(event.duration_ms),
                    int(event.input_tokens),
                    int(event.output_tokens),
                    int(event.total_tokens),
                    1 if event.estimated_tokens else 0,
                    float(event.cost_usd),
                    json.dumps(event.payload or {}, ensure_ascii=False, default=str),
                ),
            )
            return int(cursor.lastrowid)

    def events(self, thread_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM autoweb_run_trace
                WHERE thread_id = ? ORDER BY id
                """,
                (str(thread_id),),
            ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["estimated_tokens"] = bool(event["estimated_tokens"])
            event["payload"] = json.loads(event.pop("payload_json"))
            events.append(event)
        return events

    def summarize(self, thread_id: str) -> RunUsageSummary:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS event_count,
                    SUM(CASE WHEN event_type = 'llm' THEN 1 ELSE 0 END) AS llm_calls,
                    SUM(CASE WHEN event_type = 'browser_action' THEN 1 ELSE 0 END) AS browser_actions,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    SUM(CASE WHEN estimated_tokens = 1 THEN 1 ELSE 0 END) AS estimated_calls,
                    COALESCE(SUM(cost_usd), 0) AS cost_usd,
                    COALESCE(SUM(duration_ms), 0) AS total_duration_ms
                FROM autoweb_run_trace WHERE thread_id = ?
                """,
                (str(thread_id),),
            ).fetchone()
        return RunUsageSummary(
            thread_id=str(thread_id),
            event_count=int(row["event_count"] or 0),
            llm_call_count=int(row["llm_calls"] or 0),
            browser_action_count=int(row["browser_actions"] or 0),
            input_tokens=int(row["input_tokens"] or 0),
            output_tokens=int(row["output_tokens"] or 0),
            total_tokens=int(row["total_tokens"] or 0),
            estimated_call_count=int(row["estimated_calls"] or 0),
            cost_usd=round(float(row["cost_usd"] or 0), 8),
            total_duration_ms=round(float(row["total_duration_ms"] or 0), 3),
        )

    def summary_dict(self, thread_id: str) -> dict[str, Any]:
        return asdict(self.summarize(thread_id))


_default_store: RunTraceStore | None = None
_default_lock = threading.Lock()


def configure_run_trace_store(store: RunTraceStore | None) -> None:
    global _default_store
    with _default_lock:
        _default_store = store


def get_run_trace_store() -> RunTraceStore | None:
    global _default_store
    if _default_store is not None:
        return _default_store
    try:
        from config import (
            LLM_PRICING,
            RUN_TRACE_DB_PATH,
            RUN_TRACE_ENABLED,
        )
    except Exception:
        return None
    if not RUN_TRACE_ENABLED:
        return None
    with _default_lock:
        if _default_store is None:
            _default_store = RunTraceStore(
                RUN_TRACE_DB_PATH,
                pricing=LLM_PRICING,
            )
    return _default_store


def _thread_id(config: Mapping[str, Any] | None) -> str:
    configurable = (config or {}).get("configurable") or {}
    return str(configurable.get("thread_id") or "unscoped")


def traced_llm_invoke(
    llm: Any,
    messages: Any,
    *,
    node: str,
    state: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
    store: RunTraceStore | None = None,
) -> Any:
    """Invoke an LLM and persist real or explicitly estimated token usage."""
    trace_store = store if store is not None else get_run_trace_store()
    input_text = _message_text(messages)
    model = _model_name(llm)
    started_at = _utc_now()
    start = time.perf_counter()
    try:
        response = llm.invoke(messages)
    except Exception as exc:
        if trace_store is not None:
            trace_store.append(
                TraceEvent(
                    thread_id=_thread_id(config),
                    event_type="llm_error",
                    node=node,
                    model=model,
                    started_at=started_at,
                    duration_ms=(time.perf_counter() - start) * 1000,
                    payload={"error_type": type(exc).__name__, "error": str(exc)},
                )
            )
        raise
    duration_ms = (time.perf_counter() - start) * 1000
    input_tokens, output_tokens, total_tokens, estimated = _usage_from_response(
        response,
        input_text,
    )
    if trace_store is not None:
        trace_store.append(
            TraceEvent(
                thread_id=_thread_id(config),
                event_type="llm",
                node=node,
                model=model,
                started_at=started_at,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                estimated_tokens=estimated,
                cost_usd=trace_store.calculate_cost(
                    model,
                    input_tokens,
                    output_tokens,
                ),
                payload={
                    "task": str((state or {}).get("user_task") or "")[:500],
                },
            )
        )
    return response


def trace_browser_action(
    *,
    config: Mapping[str, Any] | None,
    state: Mapping[str, Any],
    action: Mapping[str, Any],
    result: Mapping[str, Any],
    duration_ms: float,
    store: RunTraceStore | None = None,
) -> None:
    trace_store = store if store is not None else get_run_trace_store()
    if trace_store is None:
        return
    trace_store.append(
        TraceEvent(
            thread_id=_thread_id(config),
            event_type="browser_action",
            node="Executor",
            model="dp-cli",
            started_at=_utc_now(),
            duration_ms=duration_ms,
            payload={
                "skill": action.get("skill"),
                "request_id": action.get("request_id"),
                "ok": bool(result.get("ok")),
                "error_code": ((result.get("error") or {}).get("code")),
                "url": state.get("current_url"),
            },
        )
    )
