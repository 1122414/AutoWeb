"""Deterministic public-site crawl smoke for the AutoWeb -> dp_cli boundary.

Default target is Books to Scrape, a deliberately scrape-friendly test site.
No LLM is used. The smoke validates list-region detection, list extraction,
detail navigation identity, semantic detail extraction, and session cleanup.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parents[2]))

from skills.dpcli_executor import DPCLIExecutor


TARGET_URL = os.getenv("DPCLI_PUBLIC_SMOKE_URL", "https://books.toscrape.com/")
DETAIL_LIMIT = max(1, int(os.getenv("DPCLI_PUBLIC_SMOKE_DETAILS", "3")))


def _http_url(value: object) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _content_item_url(value: object) -> bool:
    if not _http_url(value):
        return False
    parsed = urlparse(str(value))
    path_segments = {
        segment.lower()
        for segment in parsed.path.split("/")
        if segment
    }
    return not bool(
        path_segments
        & {
            "author",
            "authors",
            "writer",
            "user",
            "profile",
            "category",
            "categories",
            "genre",
            "rank",
            "ranking",
            "search",
            "tag",
            "topic",
            "help",
            "login",
            "logout",
            "register",
        }
    )


def _require_ok(payload: dict, stage: str) -> dict:
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError(
            f"{stage} failed: "
            f"{json.dumps((payload or {}).get('error'), ensure_ascii=False)}"
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"{stage} returned no data object")
    return data


def _best_region(snapshot_data: dict) -> dict:
    index = snapshot_data.get("index") or {}
    regions = [
        region
        for region in index.get("data_regions") or []
        if isinstance(region, dict)
        and region.get("ref")
        and int(region.get("item_count") or 0) >= 3
        and any(
            _content_item_url(sample.get("url"))
            for sample in region.get("sample_items") or []
            if isinstance(sample, dict)
        )
    ]
    if not regions:
        raise RuntimeError("snapshot produced no data region with valid http(s) samples")
    return max(
        regions,
        key=lambda region: (
            int(region.get("score") or 0),
            int(region.get("item_count") or 0),
        ),
    )


def main() -> int:
    session = f"autoweb-public-smoke-{uuid.uuid4().hex[:8]}"
    executor = DPCLIExecutor(session=session, headless=True)
    summary: dict = {"target_url": TARGET_URL, "session": session}
    close_result = None
    try:
        opened = _require_ok(executor.open(TARGET_URL, wait_time=1.0), "open")
        summary["opened_url"] = (opened.get("page") or {}).get("url")

        snapshot = _require_ok(
            executor.snapshot(mode="agent_summary", wait_time=1.0),
            "snapshot",
        )
        region = _best_region(snapshot)
        summary["region"] = {
            key: region.get(key)
            for key in ("ref", "kind", "item_count", "score", "why")
        }

        extracted = _require_ok(
            executor.extract(
                str(region["ref"]),
                schema=["title", "url"],
                limit=10,
            ),
            "extract",
        )
        items = [
            item
            for item in extracted.get("items") or []
            if isinstance(item, dict)
            and str(item.get("title") or "").strip().lower()
            not in {"", "link", "image", "cover", "cover image", "details", "read more"}
            and _content_item_url(item.get("url"))
        ]
        unique_urls = {str(item["url"]).rstrip("/") for item in items}
        if len(items) < DETAIL_LIMIT or len(unique_urls) != len(items):
            raise RuntimeError(
                f"list extraction quality failed: items={len(items)}, "
                f"unique_urls={len(unique_urls)}"
            )
        summary["list_items"] = len(items)

        found_next = _require_ok(executor.find(text="next"), "find-next")
        next_candidates = [
            node
            for node in found_next.get("nodes") or []
            if isinstance(node, dict)
            and node.get("ref_type") == "element"
            and node.get("role") == "link"
            and str(node.get("text") or node.get("name") or "").strip().lower() == "next"
        ]
        if not next_candidates:
            raise RuntimeError("pagination next link was not found")
        clicked_next = _require_ok(
            executor.click(ref=str(next_candidates[0]["ref"])),
            "click-next",
        )
        page_two_url = str((clicked_next.get("page") or {}).get("url") or "")
        if page_two_url == summary["opened_url"] or not _http_url(page_two_url):
            raise RuntimeError(f"pagination did not change page URL: {page_two_url!r}")

        snapshot_two = _require_ok(
            executor.snapshot(mode="agent_summary", wait_time=0.5),
            "snapshot-page-two",
        )
        region_two = _best_region(snapshot_two)
        extracted_two = _require_ok(
            executor.extract(
                str(region_two["ref"]),
                schema=["title", "url"],
                limit=10,
            ),
            "extract-page-two",
        )
        page_two_items = [
            item
            for item in extracted_two.get("items") or []
            if isinstance(item, dict)
            and str(item.get("title") or "").strip().lower()
            not in {"", "link", "image", "cover", "cover image", "details", "read more"}
            and _content_item_url(item.get("url"))
        ]
        page_two_urls = {str(item["url"]).rstrip("/") for item in page_two_items}
        if len(page_two_items) < DETAIL_LIMIT or unique_urls & page_two_urls:
            raise RuntimeError(
                f"page-two extraction quality failed: items={len(page_two_items)}, "
                f"overlap={len(unique_urls & page_two_urls)}"
            )
        summary["list_pages"] = 2
        summary["page_two_url"] = page_two_url
        summary["unique_list_items"] = len(unique_urls | page_two_urls)

        details = _require_ok(
            executor.batch_detail_extract(
                items=items[:DETAIL_LIMIT],
                source_url=str((snapshot.get("page") or {}).get("url") or TARGET_URL),
                limit=DETAIL_LIMIT,
                schema=["title", "description"],
                extractor="legacy-js",
                navigation_mode="direct",
                fallback_mode="direct",
                wait_time=0.25,
                max_retries=1,
                item_timeout=60,
                command_timeout=max(120, DETAIL_LIMIT * 70),
            ),
            "batch-detail-extract",
        )
        detail_rows = details.get("items") or []
        verified = [
            row
            for row in detail_rows
            if isinstance(row, dict)
            and row.get("detail_ok") is True
            and _http_url(row.get("final_url"))
            and isinstance(row.get("detail_info"), dict)
            and row["detail_info"].get("title")
            and row["detail_info"].get("description")
        ]
        if len(verified) != DETAIL_LIMIT:
            errors = [
                row.get("detail_error")
                for row in detail_rows
                if isinstance(row, dict) and not row.get("detail_ok")
            ]
            raise RuntimeError(
                f"detail verification failed: {len(verified)}/{DETAIL_LIMIT}; "
                f"errors={errors}"
            )
        summary["verified_details"] = len(verified)
        summary["final_urls"] = [row.get("final_url") for row in verified]
        summary["ok"] = True
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        summary["ok"] = False
        summary["error"] = str(exc)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1
    finally:
        close_result = executor.session_close()
        if not isinstance(close_result, dict) or not close_result.get("ok"):
            print(
                json.dumps(
                    {"session_cleanup_warning": close_result},
                    ensure_ascii=False,
                )
            )


if __name__ == "__main__":
    raise SystemExit(main())
