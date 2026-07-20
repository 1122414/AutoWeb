"""Audit drissionpage-cli projection against saved real public-site snapshots.

This is a cross-project component gate, not a replacement for the repeated live
natural-language benchmark.  It imports the selected drissionpage-cli worktree,
runs its current data-region detector and ExtractProjector against the exact raw
nodes captured during earlier public-site runs, then validates the output with
AutoWeb's original natural-language task contract.
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

from skills.dpcli_task_contract import (
    build_task_contract,
    evaluate_contract_items,
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_recorded_path(value: str, fallback: Path) -> Path:
    recorded = Path(value)
    return recorded if recorded.exists() else fallback


def _load_cli_types(cli_repo: Path):
    cli_path = str(cli_repo.resolve())
    if cli_path not in sys.path:
        sys.path.insert(0, cli_path)
    from dp_cli.projector import ExtractProjector
    from dp_cli.service import CliService

    return ExtractProjector, CliService


def _subtree_nodes(nodes: list[dict[str, Any]], target_ref: str) -> list[dict[str, Any]]:
    target = next(
        (node for node in nodes if str(node.get("ref") or "") == target_ref),
        None,
    )
    if not target:
        return []
    root_xpath = str(target.get("xpath") or "")
    if not root_xpath:
        return [target]
    prefix = root_xpath.rstrip("/") + "/"
    return [
        node
        for node in nodes
        if str(node.get("xpath") or "") == root_xpath
        or str(node.get("xpath") or "").startswith(prefix)
    ]


def _anchor_match(case: dict[str, Any], items: list[dict[str, Any]]) -> bool:
    anchors = case.get("anchor_values") or []
    if not anchors:
        return True
    corpus = json.dumps(items, ensure_ascii=False, default=str).lower()
    return any(str(anchor).lower() in corpus for anchor in anchors)


def _region_matches_target(
    nodes: list[dict[str, Any]],
    region_ref: str,
    target_ref: str,
) -> bool:
    by_ref = {
        str(node.get("ref") or ""): node
        for node in nodes
        if node.get("ref")
    }
    region_xpath = str((by_ref.get(region_ref) or {}).get("xpath") or "")
    target_xpath = str((by_ref.get(target_ref) or {}).get("xpath") or "")
    if not region_xpath or not target_xpath:
        return region_ref == target_ref
    return (
        region_xpath == target_xpath
        or region_xpath.startswith(target_xpath.rstrip("/") + "/")
        or target_xpath.startswith(region_xpath.rstrip("/") + "/")
    )


def audit_case(
    case: dict[str, Any],
    replay_case: dict[str, Any],
    cli_repo: Path,
    projector_type,
    service_type,
) -> dict[str, Any]:
    session = str(replay_case["session"])
    fallback_dir = PROJECT_ROOT / "output" / "dpcli_snapshots" / session
    fallback_index = sorted(fallback_dir.glob("ss_*.index.json"))[-1]
    index_file = _resolve_recorded_path(
        str(replay_case.get("snapshot_index") or ""),
        fallback_index,
    )
    full_file = index_file.with_name(
        index_file.name.replace(".index.json", ".full.json")
    )
    full_snapshot = _read_json(full_file)
    artifact_file = _resolve_recorded_path(
        str((full_snapshot.get("data") or {}).get("artifact_file") or ""),
        full_file,
    )
    artifact = _read_json(artifact_file)
    nodes = list(
        artifact.get("nodes")
        or ((full_snapshot.get("data") or {}).get("index") or {}).get(
            "surface_index"
        )
        or []
    )

    target_ref = str(replay_case.get("target_ref") or "")
    subtree = _subtree_nodes(nodes, target_ref)
    element_refs = [
        str(node["ref"])
        for node in subtree
        if node.get("ref") and node.get("ref_type") == "element"
    ]
    contract = build_task_contract(str(case.get("task") or ""))
    schema = list(contract.get("schema") or [])
    raw_projection = projector_type().project(
        {
            "group_ref": target_ref,
            "representative_ref": target_ref,
            "item_refs": element_refs,
        },
        subtree,
        schema,
    )
    limit = max(1, int(contract.get("per_page_limit") or 1))
    items = list(raw_projection.get("items") or [])[:limit]
    evaluation = evaluate_contract_items(
        contract,
        items,
        expected_count=limit,
    )

    # The detector is pure for this call; avoid constructing SessionManager or
    # DrissionPageAdapter, which would create runtime state outside this audit.
    service = service_type.__new__(service_type)
    regions = service._detect_data_regions(nodes)
    matching_regions = [
        str(region.get("ref") or "")
        for region in regions
        if _region_matches_target(
            nodes,
            str(region.get("ref") or ""),
            target_ref,
        )
    ]
    region_detected = bool(matching_regions)
    projection_pass = bool(
        evaluation["is_success"] and _anchor_match(case, items)
    )
    component_pass = bool(region_detected and projection_pass)
    return {
        "case": case["key"],
        "session": session,
        "cli_repo": str(cli_repo.resolve()),
        "artifact_file": str(artifact_file),
        "target_ref": target_ref,
        "subtree_node_count": len(subtree),
        "element_ref_count": len(element_refs),
        "detected_regions": [
            {
                "ref": region.get("ref"),
                "kind": region.get("kind"),
                "item_count": region.get("item_count"),
                "score": region.get("score"),
            }
            for region in regions
        ],
        "matching_region_refs": matching_regions,
        "region_detected": region_detected,
        "projected_item_count": len(items),
        "projected_items": items,
        "contract_evaluation": evaluation,
        "known_anchor_present": _anchor_match(case, items),
        "projection_pass": projection_pass,
        "component_pass": component_pass,
    }


def audit_matrix(
    replay_file: Path,
    matrix_file: Path,
    cli_repo: Path,
) -> dict[str, Any]:
    replay = _read_json(replay_file)
    matrix = _read_json(matrix_file)
    cases_by_key = {
        str(run["case"]["key"]): run["case"]
        for run in matrix.get("runs") or []
    }
    projector_type, service_type = _load_cli_types(cli_repo)
    cases = [
        audit_case(
            cases_by_key[str(replay_case["case"])],
            replay_case,
            cli_repo,
            projector_type,
            service_type,
        )
        for replay_case in replay.get("cases") or []
    ]
    passed = sum(bool(case.get("component_pass")) for case in cases)
    projection_passed = sum(bool(case.get("projection_pass")) for case in cases)
    region_passed = sum(bool(case.get("region_detected")) for case in cases)
    count = len(cases)
    return {
        "generated_at": datetime.now().isoformat(),
        "evidence_type": "offline_cross_project_component_audit",
        "warning": (
            "This audit uses real saved nodes but does not exercise a live "
            "browser, session lifecycle, pagination, or network stability."
        ),
        "cli_repo": str(cli_repo.resolve()),
        "source_replay": str(replay_file.resolve()),
        "source_matrix": str(matrix_file.resolve()),
        "summary": {
            "case_count": count,
            "data_region_passes": region_passed,
            "direct_projection_passes": projection_passed,
            "component_passes": passed,
            "component_pass_rate": round(passed / count * 100, 1) if count else 0.0,
        },
        "cases": cases,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit a drissionpage-cli worktree with saved snapshots."
    )
    parser.add_argument(
        "--cli-repo",
        default=str(PROJECT_ROOT.parent / "drissionpage-cli"),
    )
    parser.add_argument(
        "--replay",
        default="output/benchmarks/snapshot_projection_replay.json",
    )
    parser.add_argument(
        "--matrix",
        default="output/benchmarks/contract_matrix_once.json",
    )
    parser.add_argument(
        "--output",
        default="output/benchmarks/dpcli_saved_snapshot_audit.json",
    )
    parser.add_argument("--fail-under", type=float, default=80.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cli_repo = Path(args.cli_repo).resolve()
    replay_file = (PROJECT_ROOT / args.replay).resolve()
    matrix_file = (PROJECT_ROOT / args.matrix).resolve()
    output_file = (PROJECT_ROOT / args.output).resolve()
    result = audit_matrix(replay_file, matrix_file, cli_repo)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result["summary"], ensure_ascii=False))
    print(f"Audit result: {output_file}")
    return (
        0
        if result["summary"]["component_pass_rate"] >= args.fail_under
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
