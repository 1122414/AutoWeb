"""Replay saved public-site dp_cli snapshots without browser or network access.

This benchmark proves deterministic planning, action construction, snapshot
projection, and task-contract verification against real captured structures.
It deliberately does not count as a live end-to-end success-rate result.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.nodes._dpcli import _dpcli_policy_action_from_structured_plan
from core.nodes.verifier import _verify_dpcli_action_with_signals
from skills.dpcli_result_enricher import enrich_extract_result
from skills.dpcli_task_contract import (
    build_contract_plan,
    build_task_contract,
    evaluate_contract_items,
    merge_contract_progress,
    result_items,
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _snapshot_files(session: str) -> tuple[Path, Path]:
    session_dir = PROJECT_ROOT / "output" / "dpcli_snapshots" / session
    index_files = sorted(session_dir.glob("ss_*.index.json"))
    if not index_files:
        raise FileNotFoundError(f"no snapshot index found for session {session}")
    index_file = index_files[-1]
    planner_file = index_file.with_name(
        index_file.name.replace(".index.json", ".planner_view.json")
    )
    if not planner_file.exists():
        raise FileNotFoundError(f"planner view missing: {planner_file}")
    return index_file, planner_file


def _extract_result_from_events(run: dict[str, Any]) -> dict[str, Any]:
    for event in run.get("events") or []:
        compact = event.get("dpcli_result")
        if (
            isinstance(compact, dict)
            and compact.get("ok")
            and compact.get("action") == "extract"
            and isinstance(compact.get("items"), list)
        ):
            return {
                "ok": True,
                "action": "extract",
                "data": {
                    "page": {"url": compact.get("page_url") or ""},
                    "items": compact.get("items") or [],
                    "item_count": len(compact.get("items") or []),
                },
                "error": None,
            }
    return {
        "ok": True,
        "action": "extract",
        "data": {"items": [], "item_count": 0},
        "error": None,
    }


def _anchor_match(case: dict[str, Any], items: list[dict[str, Any]]) -> bool:
    corpus = json.dumps(items, ensure_ascii=False, default=str).lower()
    return any(
        str(anchor).lower() in corpus
        for anchor in case.get("anchor_values") or []
    )


def replay_run(run: dict[str, Any]) -> dict[str, Any]:
    case = run["case"]
    contract = build_task_contract(case.get("task", ""))
    index_file, planner_file = _snapshot_files(str(run["session"]))
    planner_view = _read_json(planner_file)
    base_state = {
        "user_task": case.get("task", ""),
        "current_url": case.get("url", ""),
        "execution_mode": "dp_cli",
        "dpcli_agent_view": planner_view,
        "dpcli_snapshot_ref": {
            "index_file": str(index_file),
            "planner_view_file": str(planner_file),
        },
        "dpcli_task_contract": contract,
        "dpcli_task_progress": {},
    }
    structured_plan, updates = build_contract_plan(base_state, contract)
    if not structured_plan:
        return {
            "case": case["key"],
            "session": run["session"],
            "replay_pass": False,
            "reason": "deterministic planner returned no action",
        }

    state = dict(base_state)
    state.update(updates)
    state["dpcli_structured_plan"] = structured_plan
    action = _dpcli_policy_action_from_structured_plan(state)
    if not action:
        return {
            "case": case["key"],
            "session": run["session"],
            "replay_pass": False,
            "reason": "structured plan did not produce a policy action",
            "structured_plan": structured_plan,
        }

    raw_result = _extract_result_from_events(run)
    enriched = enrich_extract_result(state, action, raw_result)
    items = result_items(enriched)
    per_page_evaluation = evaluate_contract_items(
        contract,
        items,
        expected_count=int(contract.get("per_page_limit") or 1),
    )

    verification_state = dict(state)
    verification_state.update({
        "generated_action": action,
        "dpcli_result": enriched,
        "dpcli_execution_evidence": {
            "after_url": case.get("url", ""),
            "result_ok": bool(enriched.get("ok")),
        },
    })
    deterministic_verification = _verify_dpcli_action_with_signals(
        verification_state,
        case.get("url", ""),
    )

    progress = merge_contract_progress({}, items, page_number=1)
    cumulative = evaluate_contract_items(
        contract,
        progress["items"],
        expected_count=int(contract.get("min_items") or 1),
    )
    target_pages = max(1, int(contract.get("target_pages") or 1))
    full_task_done = bool(
        cumulative["is_success"]
        and len(progress.get("completed_pages") or []) >= target_pages
    )

    next_step = None
    if not full_task_done:
        next_state = dict(state)
        next_state["dpcli_task_progress"] = progress
        next_plan, _next_updates = build_contract_plan(next_state, contract)
        if next_plan:
            next_step = {
                "intent": next_plan.get("step_intent"),
                "payload": next_plan.get("action_payload") or {},
            }

    return {
        "case": case["key"],
        "name": case.get("name"),
        "session": run["session"],
        "snapshot_index": str(index_file),
        "planner_intent": structured_plan.get("step_intent"),
        "target_ref": (structured_plan.get("action_payload") or {}).get(
            "target_ref"
        ),
        "policy_skill": action.get("skill"),
        "raw_item_count": len(result_items(raw_result)),
        "projected_item_count": len(items),
        "projection": (enriched.get("data") or {}).get("projection"),
        "per_page_evaluation": per_page_evaluation,
        "deterministic_verification": deterministic_verification,
        "known_anchor_present": _anchor_match(case, items),
        "full_task_done_from_saved_pages": full_task_done,
        "next_step": next_step,
        "replay_pass": bool(
            per_page_evaluation["is_success"]
            and deterministic_verification
            and deterministic_verification.get("is_success")
            and _anchor_match(case, items)
        ),
    }


def replay_matrix(matrix_file: Path) -> dict[str, Any]:
    matrix = _read_json(matrix_file)
    cases = [replay_run(run) for run in matrix.get("runs") or []]
    passed = sum(bool(case.get("replay_pass")) for case in cases)
    full_done = sum(
        bool(case.get("full_task_done_from_saved_pages")) for case in cases
    )
    return {
        "generated_at": datetime.now().isoformat(),
        "evidence_type": "offline_saved_snapshot_replay",
        "source_matrix": str(matrix_file.resolve()),
        "warning": (
            "Offline replay is structural evidence only. It does not replace "
            "the required repeated live natural-language benchmark."
        ),
        "summary": {
            "case_count": len(cases),
            "projection_replay_passes": passed,
            "projection_replay_rate": round(
                passed / len(cases) * 100, 1
            ) if cases else 0.0,
            "full_tasks_proven_by_saved_pages": full_done,
        },
        "cases": cases,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay a saved natural-language benchmark matrix offline."
    )
    parser.add_argument(
        "--matrix",
        default="output/benchmarks/contract_matrix_once.json",
    )
    parser.add_argument(
        "--output",
        default="output/benchmarks/snapshot_projection_replay.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    matrix_file = (PROJECT_ROOT / args.matrix).resolve()
    output_file = (PROJECT_ROOT / args.output).resolve()
    result = replay_matrix(matrix_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result["summary"], ensure_ascii=False))
    print(f"Replay result: {output_file}")
    return 0 if result["summary"]["projection_replay_passes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
