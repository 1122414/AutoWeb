"""Real AutoWeb Observer integration smoke backed by a dp_cli browser session.

This validates the full-snapshot persistence/index/compression path without an
LLM. It is intentionally separate from the lower-level public crawl smoke.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.environ["DPCLI_HEADLESS"] = "True"
os.environ["DPCLI_FULL_SNAPSHOT_MODE"] = "True"

sys.path.insert(0, str(Path(__file__).parents[2]))

from core.nodes._dpcli import _dpcli_planner_context, _observer_dpcli_snapshot
from skills.dpcli_executor import DPCLIExecutor
from skills.dpcli_snapshot_query import SnapshotQueryEngine
from skills.dpcli_snapshot_store import SnapshotStore


TARGET_URL = os.getenv("DPCLI_PUBLIC_SMOKE_URL", "https://books.toscrape.com/")


def main() -> int:
    session = f"autoweb-observer-smoke-{uuid.uuid4().hex[:8]}"
    executor = DPCLIExecutor(session=session, headless=True)
    summary: dict = {"target_url": TARGET_URL, "session": session}
    try:
        opened = executor.open(TARGET_URL, wait_time=0.5)
        if not opened.get("ok"):
            raise RuntimeError(f"open failed: {opened.get('error')}")

        command = _observer_dpcli_snapshot(
            {
                "execution_mode": "dp_cli",
                "dpcli_session": session,
                "dpcli_result": opened,
                "current_url": TARGET_URL,
                "user_task": "Extract the book list and detail descriptions",
                "finished_steps": [],
                "reflections": [],
                "loop_count": 0,
            }
        )
        if command is None or command.goto != "Planner":
            raise RuntimeError(f"Observer did not reach Planner: {command!r}")
        update = command.update or {}
        if update.get("_observer_source") != "dp_cli_full":
            raise RuntimeError(f"unexpected observer source: {update.get('_observer_source')}")

        snapshot_ref = update.get("dpcli_snapshot_ref") or {}
        required_files = [
            snapshot_ref.get("full_snapshot_file"),
            snapshot_ref.get("index_file"),
            snapshot_ref.get("compressed_index_file"),
            snapshot_ref.get("planner_view_file"),
        ]
        missing_files = [
            path
            for path in required_files
            if not path or not Path(str(path)).exists()
        ]
        if missing_files:
            raise RuntimeError(f"snapshot artifacts missing: {missing_files}")

        store = SnapshotStore(session=session)
        query = SnapshotQueryEngine(store)
        if not query.load_from_ref(snapshot_ref):
            raise RuntimeError("snapshot query engine could not load persisted index")
        title_matches = query.find_by_text("A Light in the Attic")
        if not title_matches:
            raise RuntimeError("persisted index could not recover a known book title")

        state_snapshot = update.get("dpcli_snapshot") or {}
        state_snapshot_size = len(
            json.dumps(state_snapshot, ensure_ascii=False, separators=(",", ":"))
        )
        if state_snapshot_size > 50_000:
            raise RuntimeError(
                f"Graph state snapshot is not compact: {state_snapshot_size} bytes"
            )

        planner_state = {
            **update,
            "user_task": "Extract the book list and detail descriptions",
            "finished_steps": [f"completed step {index}" for index in range(40)],
            "reflections": ["x" * 2000 for _ in range(20)],
            "loop_count": 3,
            "execution_mode": "dp_cli",
        }
        planner_context = _dpcli_planner_context(planner_state)
        if not planner_context or len(planner_context) > 80_000:
            raise RuntimeError(
                f"planner context budget is unhealthy: {len(planner_context)} chars"
            )

        index_payload = json.loads(
            Path(str(snapshot_ref["index_file"])).read_text(encoding="utf-8")
        )
        by_ref = index_payload.get("by_ref") or {}
        if len(by_ref) != len(set(by_ref)):
            raise RuntimeError("persisted snapshot index contains duplicate refs")

        summary.update(
            {
                "ok": True,
                "observer_source": update.get("_observer_source"),
                "snapshot_id": snapshot_ref.get("snapshot_id"),
                "indexed_refs": len(by_ref),
                "state_snapshot_bytes": state_snapshot_size,
                "planner_context_chars": len(planner_context),
                "title_matches": len(title_matches),
                "artifacts": required_files,
            }
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        summary["ok"] = False
        summary["error"] = str(exc)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1
    finally:
        close_result = executor.session_close()
        if not close_result.get("ok"):
            print(json.dumps({"session_cleanup_warning": close_result}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
