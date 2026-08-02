"""Export real AutoWeb crawl runs as complete JSON, CSV, and HTML deliverables."""

from __future__ import annotations

import argparse
import csv
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


EXTRACT_ACTIONS = {"extract", "list-items", "batch-detail-extract"}
CASE_ORDER = (
    "books_static",
    "quotes_static",
    "quotes_js",
    "products_pagination",
    "hockey_table",
    "products_three_pages",
    "quotes_infinite_scroll",
    "books_list_detail",
    "hockey_filter_two_pages",
    "products_restart_resume",
)


def _meaningful(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _clean_item(raw: dict[str, Any]) -> dict[str, Any]:
    item: dict[str, Any] = {}
    for nested_key in ("list_info", "detail_info"):
        nested = raw.get(nested_key)
        if isinstance(nested, dict):
            item.update(nested)
    item.update(raw)

    provenance = item.get("_provenance")
    clean = {
        str(key): value
        for key, value in item.items()
        if key not in {"list_info", "detail_info", "_provenance", "_quality"}
        and not str(key).startswith("_")
    }
    if isinstance(provenance, dict):
        if provenance.get("source_url") and not clean.get("source_url"):
            clean["source_url"] = provenance["source_url"]
        if provenance.get("captured_at") and not clean.get("captured_at"):
            clean["captured_at"] = provenance["captured_at"]
    return clean


def _identity(item: dict[str, Any]) -> str:
    for key in ("final_url", "detail_url", "url", "href"):
        value = str(item.get(key) or "").strip().rstrip("/")
        if value:
            return f"url:{value}"
    stable = {
        key: value
        for key, value in item.items()
        if key not in {"source_url", "captured_at"}
    }
    return "record:" + json.dumps(
        stable, ensure_ascii=False, sort_keys=True, default=str
    )


def _records(run: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    by_identity: dict[str, int] = {}
    for result in run.get("results") or []:
        if str(result.get("action") or "").lower() not in EXTRACT_ACTIONS:
            continue
        for raw in result.get("items") or []:
            if not isinstance(raw, dict):
                continue
            item = _clean_item(raw)
            identity = _identity(item)
            existing_index = by_identity.get(identity)
            if existing_index is None:
                by_identity[identity] = len(records)
                records.append(item)
                continue
            existing = records[existing_index]
            for key, value in item.items():
                if _meaningful(value):
                    existing[key] = value
    return records


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for record in records:
        for key in record:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({key: _csv_value(record.get(key)) for key in fields})


def _cell(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False)
    return html.escape(str(value if value is not None else ""))


def _record_table(records: list[dict[str, Any]]) -> str:
    fields: list[str] = []
    for record in records:
        for key in record:
            if key not in fields:
                fields.append(key)
    header = "".join(f"<th>{html.escape(key)}</th>" for key in fields)
    body = "".join(
        "<tr>"
        + "".join(f"<td>{_cell(record.get(key))}</td>" for key in fields)
        + "</tr>"
        for record in records
    )
    return f"<div class='table-wrap'><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>"


def _render_report(deliveries: list[dict[str, Any]], generated_at: str) -> str:
    total_records = sum(item["record_count"] for item in deliveries)
    total_seconds = sum(float(item["elapsed_seconds"]) for item in deliveries)
    summary_rows = "".join(
        "<tr>"
        f"<td>{index}</td>"
        f"<td>{html.escape(item['name'])}</td>"
        f"<td><a href='{html.escape(item['url'])}'>{html.escape(item['domain'])}</a></td>"
        f"<td>{html.escape(item['capability'])}</td>"
        f"<td><span class='ok'>{item['record_count']} 条</span></td>"
        f"<td>{item['accuracy_score']:.0f}%</td>"
        f"<td>{item['elapsed_seconds']:.3f}s</td>"
        f"<td><a href='data/{item['key']}.json'>JSON</a> · <a href='data/{item['key']}.csv'>CSV</a></td>"
        "</tr>"
        for index, item in enumerate(deliveries, 1)
    )
    sections = "".join(
        "<details>"
        f"<summary>{index}. {html.escape(item['name'])} — {item['record_count']} 条真实记录</summary>"
        f"<p class='task'>{html.escape(item['task'])}</p>"
        f"{_record_table(item['records'])}"
        "</details>"
        for index, item in enumerate(deliveries, 1)
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoWeb 十项真实爬虫任务交付</title>
<style>
:root{{--bg:#07111f;--panel:#101d30;--line:#243853;--text:#e8f0fb;--muted:#9db0c8;--accent:#55d6be;--blue:#75a7ff}}
*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(145deg,#07111f,#0d1929);color:var(--text);font:14px/1.55 Inter,"Microsoft YaHei",sans-serif}}
main{{max-width:1500px;margin:auto;padding:38px 24px 60px}}h1{{font-size:34px;margin:0 0 8px}}h2{{margin-top:34px}}p{{color:var(--muted)}}
.cards{{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:14px;margin:24px 0}}.card,details,.panel{{background:rgba(16,29,48,.92);border:1px solid var(--line);border-radius:14px}}
.card{{padding:18px}}.card b{{display:block;color:var(--accent);font-size:25px}}.card span{{color:var(--muted)}}.panel{{padding:12px;overflow:auto}}
table{{border-collapse:collapse;width:100%;min-width:900px}}th,td{{border-bottom:1px solid var(--line);padding:10px;text-align:left;vertical-align:top}}th{{color:#bcd0ea;background:#13233a;position:sticky;top:0}}td{{max-width:420px;overflow-wrap:anywhere}}
a{{color:var(--blue)}}.ok{{color:var(--accent);font-weight:700}}details{{padding:15px 18px;margin:12px 0}}summary{{cursor:pointer;font-size:16px;font-weight:700}}.task{{margin:12px 0}}.table-wrap{{overflow:auto;max-height:540px}}
.policy{{border-left:3px solid var(--accent);padding-left:14px}}code{{color:#b9e3ff}}@media(max-width:800px){{.cards{{grid-template-columns:1fr 1fr}}main{{padding:24px 12px}}}}
</style></head><body><main>
<h1>AutoWeb 十项真实爬虫任务交付</h1>
<p>生成时间：{html.escape(generated_at)}。所有数据均来自本轮真实 Headless Chromium 会话，不含模拟记录或夹具回放。</p>
<div class="cards"><div class="card"><b>10 / 10</b><span>真实任务完成</span></div><div class="card"><b>{total_records}</b><span>交付记录总数</span></div><div class="card"><b>100%</b><span>字段与数量验收</span></div><div class="card"><b>{total_seconds:.1f}s</b><span>任务执行总耗时</span></div></div>
<h2>执行汇总</h2><div class="panel"><table><thead><tr><th>#</th><th>任务</th><th>公开来源</th><th>能力</th><th>实际记录</th><th>完整度</th><th>耗时</th><th>下载</th></tr></thead><tbody>{summary_rows}</tbody></table></div>
<h2>合规说明</h2><div class="panel policy"><p><code>web-scraping.dev</code> 的 robots.txt 允许本次路径并声明 2 秒 Crawl-delay；<code>scrapethissite.com</code> 仅禁止 /lessons/ 与 /faq/，本次只访问 /pages/forms/；Books/Quotes 两个教学站未返回 robots 文件。AutoWeb SitePolicy 在执行时负责 robots 检查、域名节流与会话关闭。</p></div>
<h2>完整记录</h2>{sections}
</main></body></html>"""


def export(inputs: Iterable[Path], output_dir: Path) -> dict[str, Any]:
    runs_by_key: dict[str, dict[str, Any]] = {}
    source_files: list[str] = []
    for input_path in inputs:
        resolved = input_path.resolve()
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        source_files.append(str(resolved))
        for run in payload.get("runs") or []:
            key = str((run.get("case") or {}).get("key") or "")
            if key:
                runs_by_key[key] = run

    missing = [key for key in CASE_ORDER if key not in runs_by_key]
    if missing:
        raise RuntimeError("Missing completed crawl runs: " + ", ".join(missing))

    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    deliveries: list[dict[str, Any]] = []
    for key in CASE_ORDER:
        run = runs_by_key[key]
        case = run["case"]
        records = _records(run)
        expected = int(run["evaluation"]["unique_item_count"])
        if run.get("status") != "completed":
            raise RuntimeError(f"{key} did not complete: {run.get('status')}")
        if len(records) != expected:
            raise RuntimeError(
                f"{key} artifact contains {len(records)} records, expected {expected}"
            )
        checks = (run.get("evaluation") or {}).get("checks") or {}
        if any(value is not True for value in checks.values()):
            raise RuntimeError(f"{key} has failed evaluation checks")

        delivery = {
            "key": key,
            "name": case["name"],
            "url": case["url"],
            "domain": case["url"].split("/")[2],
            "task": case["task"],
            "capability": case["capability"],
            "status": run["status"],
            "record_count": len(records),
            "accuracy_score": float(run["evaluation"]["accuracy_score"]),
            "elapsed_seconds": float(run["elapsed_seconds"]),
            "restart_simulated": bool(run.get("restart_simulated")),
            "restart_count": int(run.get("restart_count") or 0),
            "records": records,
        }
        deliveries.append(delivery)
        (data_dir / f"{key}.json").write_text(
            json.dumps(delivery, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_csv(data_dir / f"{key}.csv", records)

    generated_at = datetime.now().astimezone().isoformat()
    manifest = {
        "schema_version": 1,
        "generated_at": generated_at,
        "execution": "real_headless_chromium",
        "contains_mock_data": False,
        "task_count": len(deliveries),
        "completed_task_count": sum(item["status"] == "completed" for item in deliveries),
        "total_record_count": sum(item["record_count"] for item in deliveries),
        "source_run_artifacts": source_files,
        "tasks": [
            {key: value for key, value in item.items() if key != "records"}
            for item in deliveries
        ],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (output_dir / "summary.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        fields = [
            "key", "name", "url", "capability", "status", "record_count",
            "accuracy_score", "elapsed_seconds", "restart_simulated", "restart_count",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in deliveries:
            writer.writerow({field: item[field] for field in fields})
    (output_dir / "all_tasks.json").write_text(
        json.dumps(deliveries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "real_crawl_report.html").write_text(
        _render_report(deliveries, generated_at), encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = export(args.inputs, args.output_dir.resolve())
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
