"""Durable Task Run persistence for LangGraph checkpoints and run manifests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from langgraph.checkpoint.sqlite import SqliteSaver


SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def build_verified_action_key(values: Mapping[str, Any]) -> str | None:
    """Build a stable key for the latest browser result stored in graph state."""
    result = values.get("dpcli_result")
    if not isinstance(result, Mapping) or not result.get("ok"):
        return None
    action = str(result.get("action") or "")
    progress = values.get("dpcli_task_progress") or {}
    payload = {
        "task_started_at": values.get("_task_started_at"),
        "session": values.get("dpcli_session"),
        "action": action,
        "page_url": values.get("current_url"),
        "completed_pages": _json_list(progress.get("completed_pages")),
        "item_count": len(_json_list(progress.get("items"))),
        "request_id": values.get("dpcli_request_id"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class RunManifest:
    thread_id: str
    schema_version: int
    user_task: str
    status: str
    dpcli_session: str | None
    current_url: str
    item_count: int
    completed_pages: list[int]
    next_nodes: list[str]
    last_verified_action_key: str | None
    created_at: str
    updated_at: str


class TaskRunStore:
    """Own the durable checkpoint and manifest seam for Task Runs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._checkpoint_connection = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
        )
        self._checkpoint_connection.execute("PRAGMA journal_mode=WAL")
        self._checkpoint_connection.execute("PRAGMA synchronous=NORMAL")
        self.checkpointer = SqliteSaver(self._checkpoint_connection)
        self._setup_manifest_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _setup_manifest_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS autoweb_task_runs (
                    thread_id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    user_task TEXT NOT NULL,
                    status TEXT NOT NULL,
                    dpcli_session TEXT,
                    current_url TEXT NOT NULL,
                    item_count INTEGER NOT NULL,
                    completed_pages_json TEXT NOT NULL,
                    next_nodes_json TEXT NOT NULL,
                    last_verified_action_key TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_autoweb_task_runs_updated
                ON autoweb_task_runs(updated_at DESC)
                """
            )

    def record_snapshot(
        self,
        thread_id: str,
        values: Mapping[str, Any],
        next_nodes: Iterable[str] = (),
    ) -> RunManifest:
        """Atomically upsert the queryable manifest for a graph checkpoint."""
        thread_id = str(thread_id or "").strip()
        if not thread_id:
            raise ValueError("thread_id is required")
        progress = values.get("dpcli_task_progress") or {}
        items = _json_list(progress.get("items"))
        completed_pages = [
            int(page)
            for page in _json_list(progress.get("completed_pages"))
            if str(page).isdigit()
        ]
        nodes = [str(node) for node in next_nodes if str(node)]
        if values.get("is_complete"):
            status = "completed"
        elif values.get("error") and not nodes:
            status = "failed"
        elif nodes:
            status = "interrupted"
        else:
            status = "running"
        now = _utc_now()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT created_at FROM autoweb_task_runs WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            created_at = str(existing["created_at"]) if existing else now
            manifest = RunManifest(
                thread_id=thread_id,
                schema_version=SCHEMA_VERSION,
                user_task=str(values.get("user_task") or ""),
                status=status,
                dpcli_session=(
                    str(values.get("dpcli_session"))
                    if values.get("dpcli_session")
                    else None
                ),
                current_url=str(values.get("current_url") or ""),
                item_count=len(items),
                completed_pages=completed_pages,
                next_nodes=nodes,
                last_verified_action_key=build_verified_action_key(values),
                created_at=created_at,
                updated_at=now,
            )
            connection.execute(
                """
                INSERT INTO autoweb_task_runs (
                    thread_id, schema_version, user_task, status, dpcli_session,
                    current_url, item_count, completed_pages_json, next_nodes_json,
                    last_verified_action_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    user_task=excluded.user_task,
                    status=excluded.status,
                    dpcli_session=excluded.dpcli_session,
                    current_url=excluded.current_url,
                    item_count=excluded.item_count,
                    completed_pages_json=excluded.completed_pages_json,
                    next_nodes_json=excluded.next_nodes_json,
                    last_verified_action_key=excluded.last_verified_action_key,
                    updated_at=excluded.updated_at
                """,
                (
                    manifest.thread_id,
                    manifest.schema_version,
                    manifest.user_task,
                    manifest.status,
                    manifest.dpcli_session,
                    manifest.current_url,
                    manifest.item_count,
                    json.dumps(manifest.completed_pages),
                    json.dumps(manifest.next_nodes),
                    manifest.last_verified_action_key,
                    manifest.created_at,
                    manifest.updated_at,
                ),
            )
        return manifest

    def get_manifest(self, thread_id: str) -> RunManifest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM autoweb_task_runs WHERE thread_id = ?",
                (str(thread_id),),
            ).fetchone()
        return self._row_to_manifest(row) if row else None

    def recent(self, limit: int = 10) -> list[RunManifest]:
        limit = max(1, min(int(limit), 100))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM autoweb_task_runs ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_manifest(row) for row in rows]

    @staticmethod
    def _row_to_manifest(row: sqlite3.Row) -> RunManifest:
        return RunManifest(
            thread_id=str(row["thread_id"]),
            schema_version=int(row["schema_version"]),
            user_task=str(row["user_task"]),
            status=str(row["status"]),
            dpcli_session=row["dpcli_session"],
            current_url=str(row["current_url"]),
            item_count=int(row["item_count"]),
            completed_pages=[
                int(page) for page in json.loads(row["completed_pages_json"])
            ],
            next_nodes=[
                str(node) for node in json.loads(row["next_nodes_json"])
            ],
            last_verified_action_key=row["last_verified_action_key"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def close(self) -> None:
        with self._lock:
            self._checkpoint_connection.close()

    def manifest_dict(self, thread_id: str) -> dict[str, Any] | None:
        manifest = self.get_manifest(thread_id)
        return asdict(manifest) if manifest else None

