"""Deterministic task contract for natural-language dp_cli crawl tasks.

The contract is deliberately small and JSON-serializable so the same user
constraints survive Planner rewrites, Coder actions, Executor results, and
Verifier completion decisions.
"""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urlparse


_HTTP_URL_RE = re.compile(r"https?://[^\s，。；;）)】\]>\"']+", re.IGNORECASE)
_COUNT_UNIT = (
    r"(?:条|个|本|行|部|篇|首|道|则|项|款|"
    r"items?|rows?|books?|products?|quotes?)"
)

_FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "title": ("title", "name", "book_title", "product_name"),
    "url": ("url", "href", "link", "detail_url", "final_url"),
    "price": ("price", "amount", "cost"),
    "text": ("text", "quote", "content", "正文"),
    "author": ("author", "writer", "作者"),
    "tags": ("tags", "tag", "标签"),
    "team": ("team", "team_name", "name", "title", "球队名称"),
    "year": ("year", "年份"),
    "wins": ("wins", "win", "胜场"),
    "losses": ("losses", "loss", "负场"),
    "description": ("description", "summary", "简介", "描述"),
}


def _contains_any(text: str, values: Iterable[str]) -> bool:
    lower = text.lower()
    return any(value.lower() in lower for value in values)


def _extract_schema(task: str) -> list[str]:
    text = str(task or "")
    lower = text.lower()
    schema: list[str] = []

    is_team_task = _contains_any(text, ("球队", "team name", "team stats"))
    is_quote_task = _contains_any(text, ("名言", "quote"))
    is_product_task = _contains_any(text, ("商品", "产品", "product"))

    if is_team_task:
        schema.append("team")
    elif is_quote_task and _contains_any(text, ("正文", "内容", "text", "quote")):
        schema.append("text")
    elif _contains_any(
        text,
        (
            "标题",
            "书名",
            "名称",
            "title",
            "name",
        ),
    ) or is_product_task:
        schema.append("title")

    if _contains_any(text, ("价格", "价钱", "price", "cost")):
        schema.append("price")
    if _contains_any(text, ("正文", "名言内容", "quote text")) and "text" not in schema:
        schema.append("text")
    if _contains_any(text, ("作者", "author", "writer")):
        schema.append("author")
    if _contains_any(text, ("标签", "tags", "tag")):
        schema.append("tags")
    if _contains_any(text, ("年份", "年代", "year")):
        schema.append("year")
    if _contains_any(text, ("胜场", "胜利场次", "wins", " win ")):
        schema.append("wins")
    if _contains_any(text, ("负场", "失败场次", "losses", " loss ")):
        schema.append("losses")
    if _contains_any(text, ("简介", "描述", "description", "summary")):
        schema.append("description")
    if _contains_any(text, ("url", "链接", "href", "link")):
        schema.append("url")

    if not schema and _contains_any(
        lower, ("提取", "抓取", "爬取", "extract", "scrape", "collect")
    ):
        schema = ["title", "url"]
    return list(dict.fromkeys(schema))


def _extract_page_count(task: str) -> int:
    text = str(task or "")
    pages = [
        int(value)
        for value in re.findall(r"第\s*(\d+)\s*页", text, flags=re.IGNORECASE)
    ]
    english_pages = [
        int(value)
        for value in re.findall(r"page\s*(\d+)", text, flags=re.IGNORECASE)
    ]
    pages.extend(english_pages)
    pages.extend(
        int(value)
        for value in re.findall(
            r"(?:前|连续(?:抓取|爬取|翻取)?|抓取|爬取|翻取|翻完|共|总共)"
            r"\s*(\d+)\s*页",
            text,
            flags=re.IGNORECASE,
        )
    )
    pages.extend(
        int(value)
        for value in re.findall(
            r"(?:first\s+)?(\d+)\s+pages?",
            text,
            flags=re.IGNORECASE,
        )
    )
    if _contains_any(text, ("两页", "2页", "two pages")):
        pages.append(2)
    return max(pages or [1])


def _collection_mode(task: str, target_pages: int) -> str:
    if _contains_any(
        task,
        (
            "无限滚动",
            "持续向下滚动",
            "滚动加载",
            "下拉加载",
            "滚动到底",
            "infinite scroll",
            "scroll to load",
        ),
    ):
        return "infinite_scroll"
    if target_pages > 1:
        return "pagination"
    return "single_page"


def _extract_scroll_rounds(task: str) -> int:
    rounds = _first_int(
        (
            r"(?:最多|至多|不超过)?\s*滚动\s*(\d+)\s*(?:轮|次)",
            r"(?:最多|至多|不超过)?\s*(\d+)\s*(?:轮|次)\s*滚动",
            r"(?:max(?:imum)?\s*)?(\d+)\s*scrolls?",
        ),
        str(task or ""),
    )
    return max(1, min(int(rounds or 6), 50))


def _extract_filter(task: str) -> Optional[Dict[str, Any]]:
    text = str(task or "")
    if not _contains_any(text, ("筛选", "过滤", "搜索", "filter", "search")):
        return None

    quoted = re.search(
        r"(?:筛选|过滤|搜索|filter|search)[^，。；;\n]{0,24}?"
        r"[“\"']([^”\"']+)[”\"']",
        text,
        flags=re.IGNORECASE,
    )
    value = quoted.group(1).strip() if quoted else ""
    if not value:
        unquoted = re.search(
            r"(?:关键词|关键字|包含|等于|为)\s*(?:是|：|:)?\s*"
            r"([A-Za-z0-9_\-]{2,80})",
            text,
            flags=re.IGNORECASE,
        )
        value = unquoted.group(1).strip() if unquoted else ""
    if not value:
        return None

    field_hint = ""
    for hint in ("搜索框", "筛选框", "关键词", "关键字", "search"):
        if hint.lower() in text.lower():
            field_hint = hint
            break
    return {
        "kind": "text",
        "value": value,
        "field_hint": field_hint,
        "submit": True,
    }


def _first_int(patterns: Iterable[str], text: str) -> Optional[int]:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                continue
    return None


def _extract_counts(task: str, target_pages: int) -> tuple[int, int, int]:
    text = str(task or "")
    per_page = _first_int(
        (
            rf"每\s*页[^\d]{{0,8}}(\d+)\s*{_COUNT_UNIT}",
            rf"(?:第\s*[一1]\s*页|当前\s*第?\s*[一1]\s*页)"
            rf"[^\d]{{0,16}}(\d+)\s*{_COUNT_UNIT}",
            rf"前\s*(\d+)\s*{_COUNT_UNIT}",
            rf"first\s+(\d+)\s*{_COUNT_UNIT}",
        ),
        text,
    )
    total = _first_int(
        (
            rf"(?:合计|总计|总共)\s*(?:至少|不少于|约)?\s*(\d+)\s*{_COUNT_UNIT}",
            rf"(?:得到|获得|收集到|累计)\s*(?:至少|不少于)?\s*(\d+)\s*{_COUNT_UNIT}",
            rf"(?:at\s+least|total(?:ly)?)\s*(\d+)\s*{_COUNT_UNIT}",
        ),
        text,
    )

    quantities = [
        int(value)
        for value in re.findall(rf"(\d+)\s*{_COUNT_UNIT}", text, flags=re.IGNORECASE)
    ]
    if per_page is None and quantities:
        per_page = quantities[0]
    if total is None:
        if target_pages > 1 and per_page:
            total = per_page * target_pages
        elif quantities:
            total = quantities[-1]
        else:
            total = 1
    if per_page is None:
        per_page = max(1, math.ceil(total / max(target_pages, 1)))

    # A crawl contract uses a bounded target even when the wording says
    # "at least": once the requested amount is reached, autonomous work stops.
    return total, total, per_page


def _detail_required(task: str) -> bool:
    text = str(task or "")
    negative = _contains_any(
        text,
        (
            "不要进入详情",
            "不进入详情",
            "无需进入详情",
            "不要打开详情",
            "不打开详情",
            "只提取详情链接",
            "仅提取详情链接",
            "只要详情链接",
            "do not open detail",
            "without opening detail",
        ),
    )
    if negative:
        return False

    # "详情链接" is a list-page URL field, not a request to crawl details.
    scrubbed = re.sub(r"详情\s*(?:页)?\s*(?:链接|url)", "", text, flags=re.IGNORECASE)
    return _contains_any(
        scrubbed,
        (
            "进入详情页",
            "打开详情页",
            "逐个详情",
            "详情信息",
            "详情内容",
            "detail page",
            "open each detail",
        ),
    ) or (
        _contains_any(scrubbed, ("详情", "detail"))
        and _contains_any(scrubbed, ("简介", "描述", "正文", "description", "summary"))
    )


def build_task_contract(task: str) -> Dict[str, Any]:
    text = str(task or "").strip()
    match = _HTTP_URL_RE.search(text)
    target_url = match.group(0).rstrip("/,") if match else ""
    if target_url and urlparse(target_url).path in {"", "/"} and not target_url.endswith("/"):
        target_url += "/"
    target_pages = _extract_page_count(text)
    min_items, max_items, per_page_limit = _extract_counts(text, target_pages)
    schema = _extract_schema(text)
    detail_required = _detail_required(text)
    detail_schema = (
        [
            field
            for field in schema
            if field in {"description", "text", "author", "tags"}
        ]
        if detail_required
        else []
    )
    list_schema = [field for field in schema if field not in set(detail_schema)]
    if detail_required:
        for required in ("title", "url"):
            if required not in list_schema:
                list_schema.append(required)
            if required not in schema:
                schema.append(required)
    collection_mode = _collection_mode(text, target_pages)
    return {
        "version": 2,
        "task": text,
        "target_url": target_url,
        "schema": schema,
        "list_schema": list_schema or schema,
        "detail_schema": detail_schema,
        "min_items": min_items,
        "max_items": max_items,
        "per_page_limit": per_page_limit,
        "target_pages": target_pages,
        "collection_mode": collection_mode,
        "max_scroll_rounds": (
            _extract_scroll_rounds(text)
            if collection_mode == "infinite_scroll"
            else 0
        ),
        "max_stagnant_rounds": 2,
        "filter": _extract_filter(text),
        "detail_required": detail_required,
        "requires_javascript_wait": _contains_any(
            text, ("javascript", "动态内容", "动态渲染", "js 渲染", "js-rendered")
        ),
        "recovery": {
            "enabled": True,
            "resume_from": "last_verified_action",
            "max_action_retries": 3,
        },
    }


def _meaningful(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _canonical_value(item: Dict[str, Any], field: str) -> Any:
    normalized = {str(key).strip().lower(): value for key, value in item.items()}
    for alias in _FIELD_ALIASES.get(field, (field,)):
        value = normalized.get(alias.lower())
        if _meaningful(value):
            return value
    return None


def _unique_items(items: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    result: list[Dict[str, Any]] = []
    by_identity: Dict[str, int] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        identity = str(
            _canonical_value(item, "url")
            or json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        ).strip().rstrip("/")
        if not identity:
            continue
        existing_index = by_identity.get(identity)
        if existing_index is None:
            by_identity[identity] = len(result)
            result.append(dict(item))
            continue
        existing = result[existing_index]
        for key, value in item.items():
            if _meaningful(value):
                existing[key] = value
    return result


def evaluate_contract_items(
    contract: Dict[str, Any],
    items: Iterable[Dict[str, Any]],
    expected_count: Optional[int] = None,
) -> Dict[str, Any]:
    unique_items = _unique_items(items)
    schema = [str(field) for field in contract.get("schema") or []]
    field_coverage: Dict[str, float] = {}
    for field in schema:
        populated = sum(
            _meaningful(_canonical_value(item, field)) for item in unique_items
        )
        field_coverage[field] = (
            populated / len(unique_items) if unique_items else 0.0
        )

    required_count = int(
        expected_count
        if expected_count is not None
        else contract.get("min_items") or 1
    )
    count_ok = len(unique_items) >= required_count
    fields_ok = bool(schema) and all(value >= 0.8 for value in field_coverage.values())
    is_success = count_ok and fields_ok
    failures = []
    if not count_ok:
        failures.append(f"item count {len(unique_items)}/{required_count}")
    if not fields_ok:
        failures.append("required field coverage below 80%")
    return {
        "is_success": is_success,
        "summary": (
            f"task contract satisfied ({len(unique_items)} items)"
            if is_success
            else "; ".join(failures)
        ),
        "item_count": len(unique_items),
        "required_count": required_count,
        "field_coverage": field_coverage,
        "items": unique_items,
    }


def merge_contract_progress(
    progress: Optional[Dict[str, Any]],
    items: Iterable[Dict[str, Any]],
    page_number: int,
) -> Dict[str, Any]:
    merged = deepcopy(progress or {})
    merged["items"] = _unique_items(
        list(merged.get("items") or []) + list(items or [])
    )
    completed_pages = {
        int(value)
        for value in merged.get("completed_pages") or []
        if str(value).isdigit()
    }
    completed_pages.add(max(1, int(page_number or 1)))
    merged["completed_pages"] = sorted(completed_pages)
    merged["active_page"] = max(1, int(page_number or 1))
    return merged


def _current_url(state: Dict[str, Any]) -> str:
    view = state.get("dpcli_agent_view") or {}
    identity = view.get("page_identity") or view.get("page") or {}
    return str(
        (identity.get("url") if isinstance(identity, dict) else "")
        or state.get("current_url")
        or ""
    )


def _same_target(current_url: str, target_url: str) -> bool:
    if not current_url or not target_url:
        return False
    current = urlparse(current_url)
    target = urlparse(target_url)
    return bool(
        current.scheme in {"http", "https"}
        and current.netloc == target.netloc
        and (
            current.path.rstrip("/") == target.path.rstrip("/")
            or current.path.rstrip("/").startswith(target.path.rstrip("/") + "/")
        )
    )


def _region_text(region: Dict[str, Any]) -> str:
    samples = " ".join(
        str(sample.get("text") or sample.get("name") or "")
        for sample in region.get("samples") or region.get("sample_items") or []
        if isinstance(sample, dict)
    )
    return " ".join(
        str(region.get(key) or "")
        for key in ("name", "kind", "tag", "role", "why")
    ) + " " + samples


def _region_score(
    region: Dict[str, Any],
    contract: Dict[str, Any],
    required_count: int,
) -> int:
    if "extract" not in set(region.get("available_actions") or []):
        return -10000
    tag = str(region.get("tag") or "").lower()
    role = str(region.get("role") or "").lower()
    kind = str(region.get("kind") or "").lower()
    text = _region_text(region).lower()
    schema = set(contract.get("schema") or [])

    score = int(region.get("source_score") or region.get("score") or 0)
    if tag in {"nav", "header", "footer"} or role in {
        "navigation",
        "banner",
        "contentinfo",
        "tablist",
    }:
        score -= 1500
    if {"team", "year", "wins", "losses"} & schema:
        score += 1800 if kind == "table" or tag == "table" or role == "table" else -800
        score += sum(token in text for token in ("team", "year", "wins", "losses")) * 80
    if {"text", "author", "tags"} & schema:
        if kind in {"repeated_structure", "list", "card_grid"}:
            score += 650
        score += sum(token in text for token in ("quote", "author", "tags", "albert")) * 80
    if "price" in schema:
        score += 750 if kind in {"card_grid", "list", "repeated_structure"} else 0
        if re.search(r"\b\d+\.\d{2}\b", text):
            score += 250
    if {"title", "url"} <= schema:
        score += 500 if kind in {"list", "card_grid"} else 120
    item_count = int(region.get("item_count") or 0)
    if item_count >= required_count:
        score += 220
    score -= min(abs(item_count - required_count), 100)
    return score


def _select_region(
    state: Dict[str, Any],
    contract: Dict[str, Any],
    required_count: int,
) -> Optional[Dict[str, Any]]:
    view = state.get("dpcli_agent_view") or {}
    capability_map = view.get("capability_map") or {}
    progress = state.get("dpcli_task_progress") or {}
    failed_refs = set(progress.get("failed_region_refs") or [])
    regions = [
        region
        for region in capability_map.get("data_regions") or []
        if isinstance(region, dict)
        and region.get("ref")
        and region.get("ref") not in failed_refs
    ]
    ranked = sorted(
        regions,
        key=lambda item: _region_score(item, contract, required_count),
        reverse=True,
    )
    if ranked and _region_score(ranked[0], contract, required_count) > 0:
        return ranked[0]

    snapshot_region = _snapshot_projection_region(
        state,
        contract,
        required_count,
        failed_refs,
    )
    if snapshot_region:
        return snapshot_region

    # A semantic content region is a useful last deterministic target for
    # table-like tasks when the data-region detector is temporarily incomplete.
    if {"team", "year", "wins", "losses"} & set(contract.get("schema") or []):
        for region in capability_map.get("content_regions") or []:
            if not isinstance(region, dict) or not region.get("ref"):
                continue
            if region.get("ref") in failed_refs:
                continue
            text = _region_text(region).lower()
            if any(token in text for token in ("team", "hockey", "球队")):
                return {
                    **region,
                    "available_actions": ["extract"],
                    "kind": region.get("kind") or "content_region",
                }
    return None


def _snapshot_projection_region(
    state: Dict[str, Any],
    contract: Dict[str, Any],
    required_count: int,
    failed_refs: set[str],
) -> Optional[Dict[str, Any]]:
    snapshot_ref = state.get("dpcli_snapshot_ref") or {}
    index_file = snapshot_ref.get("index_file")
    if not index_file:
        return None
    try:
        payload = json.loads(Path(str(index_file)).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    by_ref = payload.get("by_ref") if isinstance(payload, dict) else None
    if not isinstance(by_ref, dict):
        return None
    nodes = [
        node
        for node in by_ref.values()
        if isinstance(node, dict)
        and re.fullmatch(r"r\d+", str(node.get("ref") or ""))
        and node.get("ref") not in failed_refs
    ]
    schema = set(contract.get("schema") or [])
    if {"team", "year", "wins", "losses"} & schema:
        for node in nodes:
            if (
                str(node.get("tag") or "").lower() == "table"
                or str(node.get("role") or "").lower() == "table"
            ):
                return {
                    **node,
                    "kind": "table",
                    "item_count": required_count,
                    "available_actions": ["extract"],
                    "source_score": 5000,
                }
    if {"text", "author", "tags"} & schema:
        candidates = []
        for node in nodes:
            text = str(node.get("text") or "")
            if (
                str(node.get("tag") or "").lower() == "div"
                and text.count("Tags:") >= min(3, required_count)
                and text.lower().count(" by ") >= min(3, required_count)
            ):
                candidates.append(node)
        if candidates:
            node = min(candidates, key=lambda item: len(str(item.get("text") or "")))
            return {
                **node,
                "kind": "repeated_structure",
                "item_count": required_count,
                "available_actions": ["extract"],
                "source_score": 5000,
            }
    return None


def _pagination_ref(
    capability_map: Dict[str, Any],
    page_number: int,
) -> Optional[str]:
    next_ref = None
    for group in capability_map.get("pagination") or []:
        if not isinstance(group, dict):
            continue
        for control in group.get("controls") or []:
            if not isinstance(control, dict) or not control.get("enabled", True):
                continue
            ref = str(control.get("ref") or "")
            label = str(control.get("label") or "").strip()
            direction = str(control.get("direction") or "").lower()
            if ref and label == str(page_number):
                return ref
            if ref and direction == "next":
                next_ref = ref
    return next_ref


def _filter_input_ref(
    capability_map: Dict[str, Any],
    filter_spec: Dict[str, Any],
) -> Optional[str]:
    hint = str(filter_spec.get("field_hint") or "").strip().lower()
    candidates: list[tuple[int, str]] = []
    for area in capability_map.get("search") or []:
        if not isinstance(area, dict):
            continue
        ref = str(area.get("input_ref") or "")
        if not re.fullmatch(r"e\d+", ref):
            continue
        text = str(area.get("input_name") or "").lower()
        candidates.append((100 + (30 if hint and hint in text else 0), ref))
    for form in capability_map.get("forms") or []:
        if not isinstance(form, dict):
            continue
        for field in form.get("inputs") or []:
            if not isinstance(field, dict):
                continue
            ref = str(field.get("ref") or "")
            if not re.fullmatch(r"e\d+", ref):
                continue
            role = str(field.get("role") or "").lower()
            input_type = str(field.get("input_type") or "").lower()
            if role not in {"textbox", "searchbox"} and input_type not in {
                "text",
                "search",
            }:
                continue
            text = str(field.get("name") or "").lower()
            score = 80
            if role == "searchbox" or input_type == "search":
                score += 20
            if hint and hint in text:
                score += 30
            candidates.append((score, ref))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _plan(
    intent: str,
    payload: Optional[Dict[str, Any]] = None,
    reason: str = "",
) -> Dict[str, Any]:
    return {
        "step_intent": intent,
        "target_request": {"required": False},
        "action_payload": dict(payload or {}),
        "reason": reason,
        "needs_rag": False,
        "needs_human_approval": False,
        "_contract_action": True,
    }


def build_contract_plan(
    state: Dict[str, Any],
    contract: Optional[Dict[str, Any]] = None,
) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    contract = deepcopy(contract or build_task_contract(state.get("user_task", "")))
    progress = deepcopy(state.get("dpcli_task_progress") or {})
    progress.setdefault("items", [])
    progress.setdefault("completed_pages", [])
    progress.setdefault("active_page", 1)
    progress.setdefault("failed_region_refs", [])
    progress.setdefault("filter_applied", not bool(contract.get("filter")))
    progress.setdefault("scroll_round", 0)
    progress.setdefault("stagnant_rounds", 0)
    progress.setdefault("list_complete", False)
    progress.setdefault("detail_complete", False)
    updates = {
        "dpcli_task_contract": contract,
        "dpcli_task_progress": progress,
    }

    target_url = str(contract.get("target_url") or "")
    current_url = _current_url(state)
    if target_url and not _same_target(current_url, target_url):
        return (
            _plan(
                "open",
                {"url": target_url},
                "deterministic task contract: open target URL",
            ),
            updates,
        )

    completed = evaluate_contract_items(
        contract,
        progress.get("items") or [],
        expected_count=int(contract.get("min_items") or 1),
    )
    target_pages = max(1, int(contract.get("target_pages") or 1))
    completed_pages = {
        int(value)
        for value in progress.get("completed_pages") or []
        if str(value).isdigit()
    }
    if completed["is_success"] and len(completed_pages) >= target_pages:
        return (
            _plan("finish", {}, "deterministic task contract satisfied"),
            updates,
        )

    active_page = max(1, int(progress.get("active_page") or 1))
    capability_map = (state.get("dpcli_agent_view") or {}).get(
        "capability_map"
    ) or {}

    filter_spec = contract.get("filter")
    if isinstance(filter_spec, dict) and not progress.get("filter_applied"):
        input_ref = _filter_input_ref(capability_map, filter_spec)
        if input_ref:
            return (
                _plan(
                    "type",
                    {
                        "ref": input_ref,
                        "text": str(filter_spec.get("value") or ""),
                        "submit": bool(filter_spec.get("submit", True)),
                        "filter_stage": "applied",
                    },
                    "deterministic task contract: apply text filter before extraction",
                ),
                updates,
            )

    if active_page in completed_pages and active_page < target_pages:
        next_page = active_page + 1
        ref = _pagination_ref(capability_map, next_page)
        if ref:
            return (
                _plan(
                    "click",
                    {"ref": ref, "page_number": next_page},
                    f"deterministic task contract: open page {next_page}",
                ),
                updates,
            )

    remaining = max(
        1,
        int(contract.get("min_items") or 1) - len(progress.get("items") or []),
    )
    limit = min(
        max(1, int(contract.get("per_page_limit") or remaining)),
        remaining,
    )
    item_count = len(_unique_items(progress.get("items") or []))
    required_count = int(contract.get("min_items") or 1)
    region = _select_region(state, contract, limit)
    if region:
        return (
            _plan(
                "extract",
                {
                    "target_ref": str(region["ref"]),
                    "schema": list(
                        contract.get("list_schema")
                        or contract.get("schema")
                        or []
                    ),
                    "limit": limit,
                },
                "deterministic task contract: extract required fields and count",
            ),
            updates,
        )

    if (
        contract.get("collection_mode") == "infinite_scroll"
        and item_count < required_count
    ):
        scroll_round = max(0, int(progress.get("scroll_round") or 0))
        max_scroll_rounds = max(1, int(contract.get("max_scroll_rounds") or 1))
        stagnant_rounds = max(0, int(progress.get("stagnant_rounds") or 0))
        max_stagnant_rounds = max(
            1, int(contract.get("max_stagnant_rounds") or 2)
        )
        if (
            scroll_round < max_scroll_rounds
            and stagnant_rounds < max_stagnant_rounds
        ):
            return (
                _plan(
                    "scroll",
                    {
                        "direction": "down",
                        "amount": 900,
                        "to": "bottom",
                        "wait_time": 1.0,
                        "round": scroll_round + 1,
                    },
                    (
                        "deterministic task contract: load more content "
                        f"(scroll {scroll_round + 1}/{max_scroll_rounds})"
                    ),
                ),
                updates,
            )

    if contract.get("requires_javascript_wait") and not progress.get("waited"):
        progress["waited"] = True
        updates["dpcli_task_progress"] = progress
        return (
            _plan(
                "wait",
                {"seconds": 1.0},
                "deterministic task contract: wait for JavaScript content",
            ),
            updates,
        )
    return (
        _plan(
            "fail",
            {
                "item_count": item_count,
                "required_count": required_count,
            },
            (
                "deterministic task contract exhausted available regions "
                f"({item_count}/{required_count} items)"
            ),
        ),
        updates,
    )


def result_items(result: Dict[str, Any]) -> list[Dict[str, Any]]:
    if not isinstance(result, dict) or not result.get("ok"):
        return []
    data = result.get("data") or {}
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        merged: Dict[str, Any] = {}
        if isinstance(item.get("list_info"), dict):
            merged.update(item["list_info"])
        if isinstance(item.get("detail_info"), dict):
            merged.update(item["detail_info"])
        merged.update(item)
        normalized.append(merged)
    return normalized
