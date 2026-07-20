"""Repair structured extraction from the persisted dp_cli snapshot index.

This is a deterministic compatibility layer for snapshots whose region
detector is correct enough to expose page content but whose generic projector
collapses repeated records into links. The same projection rules are suitable
for moving into drissionpage-cli's projector once that repository is writable.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable

from skills.dpcli_task_contract import evaluate_contract_items, result_items


def _load_nodes(state: Dict[str, Any]) -> list[Dict[str, Any]]:
    snapshot_ref = state.get("dpcli_snapshot_ref") or {}
    index_file = snapshot_ref.get("index_file")
    if not index_file:
        return []
    try:
        payload = json.loads(Path(str(index_file)).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    by_ref = payload.get("by_ref") if isinstance(payload, dict) else None
    if not isinstance(by_ref, dict):
        return []

    def ref_order(node: Dict[str, Any]) -> tuple[int, int]:
        ref = str(node.get("ref") or "")
        match = re.fullmatch(r"([er])(\d+)", ref)
        if not match:
            return (2, 10**9)
        return (0 if match.group(1) == "r" else 1, int(match.group(2)))

    return sorted(
        (dict(node) for node in by_ref.values() if isinstance(node, dict)),
        key=ref_order,
    )


def _meaningful_text(node: Dict[str, Any]) -> str:
    return str(node.get("text") or node.get("name") or "").strip()


def _project_quotes(
    nodes: list[Dict[str, Any]],
    limit: int,
) -> list[Dict[str, Any]]:
    aggregate_candidates = [
        _meaningful_text(node)
        for node in nodes
        if _meaningful_text(node).count("Tags:") >= limit
        and _meaningful_text(node).lower().count(" by ") >= limit
    ]
    for aggregate in sorted(aggregate_candidates, key=len):
        matches = re.findall(
            r"([“\"][^”\"]+[”\"])\s+by\s+(.+?)\s+\(about\)\s+"
            r"Tags:\s*(.*?)(?=\s+[“\"]|\s+Next\b|$)",
            aggregate,
            flags=re.DOTALL,
        )
        if len(matches) >= limit:
            return [
                {
                    "text": quote.strip(),
                    "author": author.strip(),
                    "tags": [value for value in tags.strip().split() if value],
                }
                for quote, author, tags in matches[:limit]
            ]

    ref_nodes = [
        node
        for node in nodes
        if re.fullmatch(r"r\d+", str(node.get("ref") or ""))
    ]
    authors = [
        (index, node)
        for index, node in enumerate(ref_nodes)
        if str(node.get("tag") or "").lower() == "small"
        and _meaningful_text(node)
    ]
    items = []
    for author_index, author_node in authors:
        before = ref_nodes[max(0, author_index - 5):author_index]
        quote = ""
        for candidate in reversed(before):
            text = _meaningful_text(candidate)
            if (
                text.startswith(("“", '"', "‘"))
                and not text.lower().startswith(("by ", "tags:"))
                and " (about)" not in text
            ):
                quote = text
                break
        if not quote:
            continue

        tags: list[str] = []
        for candidate in ref_nodes[author_index + 1:author_index + 5]:
            text = _meaningful_text(candidate)
            if text.lower().startswith("tags:"):
                tags = [
                    value
                    for value in text.split(":", 1)[1].strip().split()
                    if value
                ]
                break
            if str(candidate.get("tag") or "").lower() == "small":
                break
        items.append(
            {
                "text": quote,
                "author": _meaningful_text(author_node),
                "tags": tags,
            }
        )
        if len(items) >= limit:
            break
    return items


def _project_table(
    nodes: list[Dict[str, Any]],
    limit: int,
) -> list[Dict[str, Any]]:
    ref_nodes = [
        node
        for node in nodes
        if re.fullmatch(r"r\d+", str(node.get("ref") or ""))
    ]
    row_positions = [
        index
        for index, node in enumerate(ref_nodes)
        if str(node.get("tag") or "").lower() == "tr"
        and str(node.get("role") or "").lower() == "row"
    ]
    items = []
    for position_index, row_start in enumerate(row_positions):
        row_end = (
            row_positions[position_index + 1]
            if position_index + 1 < len(row_positions)
            else len(ref_nodes)
        )
        cells = [
            _meaningful_text(node)
            for node in ref_nodes[row_start + 1:row_end]
            if str(node.get("tag") or "").lower() == "td"
            and _meaningful_text(node)
        ]
        if len(cells) < 4 or not re.fullmatch(r"\d{4}", cells[1]):
            continue
        items.append(
            {
                "team": cells[0],
                "year": cells[1],
                "wins": cells[2],
                "losses": cells[3],
            }
        )
        if len(items) >= limit:
            break
    return items


def _price_by_title(nodes: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    ref_nodes = [
        node
        for node in nodes
        if re.fullmatch(r"r\d+", str(node.get("ref") or ""))
    ]
    prices: Dict[str, str] = {}
    for index, node in enumerate(ref_nodes):
        if str(node.get("tag") or "").lower() != "h3":
            continue
        title = _meaningful_text(node)
        if not title:
            continue
        for candidate in ref_nodes[index + 1:index + 7]:
            value = _meaningful_text(candidate)
            if re.fullmatch(r"(?:[$€£¥]\s*)?\d+(?:\.\d{2})", value):
                prices[title.casefold()] = value
                break
    return prices


def _project_products(
    nodes: list[Dict[str, Any]],
    original_items: list[Dict[str, Any]],
    limit: int,
) -> list[Dict[str, Any]]:
    prices = _price_by_title(nodes)
    projected = []
    for item in original_items:
        title = str(item.get("title") or item.get("name") or "").strip()
        price = prices.get(title.casefold())
        if not title or not price:
            continue
        enriched = dict(item)
        enriched["title"] = title
        enriched["price"] = price
        projected.append(enriched)
        if len(projected) >= limit:
            break
    return projected


def enrich_extract_result(
    state: Dict[str, Any],
    action: Dict[str, Any],
    result: Dict[str, Any],
) -> Dict[str, Any]:
    """Return the original result when it already satisfies the task contract."""
    if (
        not isinstance(result, dict)
        or str(action.get("skill") or "").lower() != "extract"
        or not result.get("ok")
    ):
        return result
    contract = state.get("dpcli_task_contract") or {}
    if not contract:
        return result

    params = action.get("params") or {}
    limit = max(
        1,
        int(params.get("limit") or contract.get("per_page_limit") or 1),
    )
    original_items = result_items(result)
    original_evaluation = evaluate_contract_items(
        contract,
        original_items,
        expected_count=limit,
    )
    if original_evaluation["is_success"]:
        return result

    nodes = _load_nodes(state)
    if not nodes:
        return result
    schema = set(contract.get("schema") or [])
    if {"team", "year", "wins", "losses"} & schema:
        projected = _project_table(nodes, limit)
        projection = "table_rows"
    elif {"text", "author", "tags"} & schema:
        projected = _project_quotes(nodes, limit)
        projection = "quote_blocks"
    elif "price" in schema:
        projected = _project_products(nodes, original_items, limit)
        projection = "product_cards"
    else:
        return result

    projected_evaluation = evaluate_contract_items(
        contract,
        projected,
        expected_count=limit,
    )
    if not projected_evaluation["is_success"]:
        return result

    enriched = deepcopy(result)
    data = enriched.setdefault("data", {})
    data["items"] = projected[:limit]
    data["item_count"] = len(data["items"])
    data["projection"] = {
        "source": "autoweb_snapshot_index",
        "kind": projection,
    }
    enriched["action"] = "extract"
    return enriched
