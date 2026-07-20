"""Generate an offline Chinese HTML report for the five complex-crawl cases."""

from __future__ import annotations

import argparse
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


CASE_ORDER = (
    "products_three_pages",
    "quotes_infinite_scroll",
    "books_list_detail",
    "hockey_filter_two_pages",
    "products_restart_resume",
)


def _escape(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def load_runs(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Load benchmark runs and retain the source artifact for traceability."""
    latest_by_case: dict[str, dict[str, Any]] = {}
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for raw_run in payload.get("runs") or []:
            run = dict(raw_run)
            case = run.get("case") or {}
            key = str(case.get("key") or "")
            if not key:
                continue
            run["source_file"] = str(path.resolve())
            latest_by_case[key] = run
    return [latest_by_case[key] for key in CASE_ORDER if key in latest_by_case]


def _action_chain(run: dict[str, Any]) -> list[str]:
    labels = {
        "open": "打开目标",
        "type": "输入并提交",
        "extract": "结构化提取",
        "click": "翻到下一页",
        "scroll": "滚动加载",
        "batch-detail-extract": "批量进入详情",
    }
    chain: list[str] = []
    for event in run.get("events") or []:
        node = event.get("node")
        if node == "__simulated_restart__":
            chain.append("模拟中断并恢复")
            continue
        if node != "Coder":
            continue
        action = event.get("generated_action") or {}
        skill = str(action.get("skill") or "")
        if skill:
            chain.append(labels.get(skill, skill))
    return chain


def _coverage_text(run: dict[str, Any]) -> str:
    coverage = (run.get("evaluation") or {}).get("field_group_coverage") or {}
    if not coverage:
        return "—"
    return " · ".join(
        f"{_escape(group)} {float(value) * 100:.0f}%"
        for group, value in coverage.items()
    )


def _sample_html(run: dict[str, Any]) -> str:
    samples = (run.get("evaluation") or {}).get("item_sample") or []
    if not samples:
        return '<p class="muted">无样例</p>'
    cards = []
    for item in samples[:3]:
        pairs = []
        for key, value in item.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            pairs.append(
                f"<div><span>{_escape(key)}</span><strong>{_escape(value)}</strong></div>"
            )
        cards.append(f'<div class="sample">{"".join(pairs)}</div>')
    return "".join(cards)


def build_report(
    runs: list[dict[str, Any]],
    *,
    generated_at: str | None = None,
) -> str:
    """Build a self-contained, offline-viewable report."""
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    completed = sum(run.get("status") == "completed" for run in runs)
    scores = [
        float((run.get("evaluation") or {}).get("accuracy_score") or 0)
        for run in runs
    ]
    average_score = sum(scores) / len(scores) if scores else 0.0
    total_items = sum(
        int((run.get("evaluation") or {}).get("unique_item_count") or 0)
        for run in runs
    )
    total_seconds = sum(float(run.get("elapsed_seconds") or 0) for run in runs)
    all_passed = bool(runs) and completed == len(runs) and all(score == 100 for score in scores)

    rows = []
    details = []
    for index, run in enumerate(runs, start=1):
        case = run.get("case") or {}
        evaluation = run.get("evaluation") or {}
        score = float(evaluation.get("accuracy_score") or 0)
        items = int(evaluation.get("unique_item_count") or 0)
        elapsed = float(run.get("elapsed_seconds") or 0)
        restart = int(run.get("restart_count") or 0)
        status_label = "通过" if run.get("status") == "completed" and score == 100 else "待核查"
        rows.append(
            f"""
            <tr>
              <td><span class="index">{index:02d}</span></td>
              <td><strong>{_escape(case.get("capability"))}</strong><small>{_escape(case.get("name"))}</small></td>
              <td>{items}</td>
              <td>{elapsed:.1f}s</td>
              <td>{score:.0f}</td>
              <td><span class="status">{status_label}</span></td>
            </tr>
            """
        )

        chain = _action_chain(run)
        checkpoint = run.get("restart_checkpoint") or {}
        checkpoint_html = ""
        if restart:
            checkpoint_html = f"""
              <div class="checkpoint">
                <b>恢复证据</b>
                第 {restart} 次重建线程时已完成页：
                <code>{_escape(checkpoint.get("completed_pages"))}</code>，
                已保存 <code>{_escape(checkpoint.get("item_count"))}</code> 条，
                活动页 <code>{_escape(checkpoint.get("active_page"))}</code>。
              </div>
            """
        chain_html = "".join(
            f'<span class="step">{position + 1}. {_escape(action)}</span>'
            for position, action in enumerate(chain)
        )
        checks = evaluation.get("checks") or {}
        check_html = "".join(
            f'<span class="check {"ok" if passed else "bad"}">'
            f'{"✓" if passed else "×"} {_escape(name)}</span>'
            for name, passed in checks.items()
        )
        details.append(
            f"""
            <article class="case-card" id="{_escape(case.get("key"))}">
              <header>
                <div><span class="eyebrow">CASE {index:02d}</span><h2>{_escape(case.get("capability"))}</h2></div>
                <div class="score">{score:.0f}<small>/100</small></div>
              </header>
              <p class="site">{_escape(case.get("name"))} · {_escape(case.get("url"))}</p>
              <section class="task">
                <span>原始自然语言任务</span>
                <p>{_escape(case.get("task"))}</p>
              </section>
              <div class="metrics">
                <div><b>{items}</b><span>唯一结果</span></div>
                <div><b>{elapsed:.1f}s</b><span>真实耗时</span></div>
                <div><b>{int(run.get("event_count") or 0)}</b><span>图事件</span></div>
                <div><b>{restart}</b><span>恢复次数</span></div>
              </div>
              <section>
                <h3>实际动作链</h3>
                <div class="chain">{chain_html}</div>
              </section>
              {checkpoint_html}
              <section>
                <h3>验收证据</h3>
                <div class="checks">{check_html}</div>
                <p class="coverage">字段覆盖：{_coverage_text(run)}</p>
              </section>
              <section>
                <h3>结果样例</h3>
                <div class="samples">{_sample_html(run)}</div>
              </section>
              <footer>原始证据：<code>{_escape(run.get("source_file"))}</code></footer>
            </article>
            """
        )

    verdict = (
        "五类复杂任务全部通过真实公开站验证"
        if all_passed
        else "验证尚未全部通过，请查看待核查项"
    )
    verdict_class = "verified" if all_passed else "warning"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AutoWeb 复杂爬取能力验证报告</title>
  <style>
    :root {{
      --ink:#15233b; --muted:#66758d; --paper:#f4f7f8; --card:#fff;
      --navy:#0e2541; --teal:#00a88f; --orange:#ff8b3d; --line:#dfe7ea;
    }}
    * {{ box-sizing:border-box; }}
    html {{ scroll-behavior:smooth; }}
    body {{ margin:0; color:var(--ink); background:var(--paper);
      font:15px/1.6 "Microsoft YaHei","PingFang SC",system-ui,sans-serif; }}
    .hero {{ padding:68px max(6vw,24px) 54px; color:white;
      background:linear-gradient(125deg,#08192f 0%,#123a55 72%,#0d5a60 100%); }}
    .hero-inner, main {{ max-width:1180px; margin:auto; }}
    .kicker,.eyebrow {{ color:#71e5d2; letter-spacing:.14em; font-size:12px; font-weight:800; }}
    h1 {{ max-width:820px; margin:10px 0 14px; font-size:clamp(34px,5vw,64px); line-height:1.08; }}
    .lead {{ max-width:780px; color:#c9d7e5; font-size:18px; }}
    .verdict {{ display:inline-flex; margin-top:22px; padding:9px 14px; border-radius:999px; font-weight:800; }}
    .verified {{ color:#092d2a; background:#71e5d2; }}
    .warning {{ color:#592800; background:#ffbd84; }}
    main {{ padding:34px 20px 72px; }}
    .summary {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-top:-62px; }}
    .summary div {{ background:var(--card); border:1px solid rgba(255,255,255,.45);
      padding:22px; border-radius:16px; box-shadow:0 12px 34px rgba(9,32,52,.1); }}
    .summary b {{ display:block; font-size:30px; color:var(--navy); }}
    .summary span,.muted {{ color:var(--muted); }}
    .panel {{ margin:28px 0; padding:24px; background:white; border:1px solid var(--line); border-radius:18px; }}
    .flow {{ display:flex; flex-wrap:wrap; gap:9px; align-items:center; }}
    .flow span {{ padding:9px 12px; background:#edf8f6; border:1px solid #cdeae5; border-radius:9px; font-weight:700; }}
    .flow i {{ color:var(--orange); font-style:normal; font-weight:900; }}
    table {{ width:100%; border-collapse:collapse; }}
    th,td {{ padding:13px 10px; text-align:left; border-bottom:1px solid var(--line); }}
    th {{ color:var(--muted); font-size:12px; letter-spacing:.08em; text-transform:uppercase; }}
    td small {{ display:block; color:var(--muted); }}
    .index {{ color:var(--orange); font-weight:900; }}
    .status {{ display:inline-block; padding:4px 9px; border-radius:999px; color:#007666; background:#e2f8f3; font-weight:800; }}
    .case-card {{ margin-top:26px; padding:28px; background:white; border:1px solid var(--line); border-radius:20px; }}
    .case-card header {{ display:flex; justify-content:space-between; align-items:flex-start; gap:20px; }}
    .case-card h2 {{ margin:2px 0; font-size:28px; }}
    .case-card h3 {{ margin:24px 0 10px; font-size:15px; }}
    .site,.coverage,footer {{ color:var(--muted); }}
    .score {{ color:var(--teal); font-size:38px; font-weight:900; }}
    .score small {{ font-size:14px; color:var(--muted); }}
    .task {{ margin:18px 0; padding:18px 20px; border-left:4px solid var(--orange); background:#fff7f0; border-radius:0 12px 12px 0; }}
    .task span {{ color:#a34d18; font-size:12px; font-weight:900; letter-spacing:.08em; }}
    .task p {{ margin:5px 0 0; font-size:17px; }}
    .metrics {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }}
    .metrics div {{ padding:13px; background:#f6f9fa; border-radius:10px; }}
    .metrics b,.metrics span {{ display:block; }}
    .metrics b {{ font-size:20px; }} .metrics span {{ color:var(--muted); font-size:12px; }}
    .chain,.checks {{ display:flex; flex-wrap:wrap; gap:7px; }}
    .step,.check {{ padding:6px 9px; border-radius:8px; background:#edf3f6; }}
    .check.ok {{ color:#067663; background:#e8f8f4; }} .check.bad {{ color:#9d2c23; background:#fff0ee; }}
    .checkpoint {{ margin-top:18px; padding:13px 15px; color:#603414; background:#fff1e4; border:1px solid #ffd1ae; border-radius:10px; }}
    .samples {{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }}
    .sample {{ padding:13px; background:#0e2541; color:#e8f2f8; border-radius:10px; overflow:hidden; }}
    .sample div {{ margin-bottom:8px; }} .sample span,.sample strong {{ display:block; }}
    .sample span {{ color:#86a3b8; font-size:11px; text-transform:uppercase; }}
    .sample strong {{ overflow-wrap:anywhere; }}
    footer {{ margin-top:20px; padding-top:14px; border-top:1px solid var(--line); font-size:12px; overflow-wrap:anywhere; }}
    code {{ font-family:Consolas,monospace; }}
    @media (max-width:760px) {{
      .summary,.metrics,.samples {{ grid-template-columns:1fr 1fr; }}
      .panel {{ overflow-x:auto; }} h1 {{ font-size:38px; }}
    }}
    @media (max-width:480px) {{
      .summary,.metrics,.samples {{ grid-template-columns:1fr; }}
    }}
  </style>
</head>
<body>
  <section class="hero">
    <div class="hero-inner">
      <div class="kicker">AUTOWEB × DRISSIONPAGE CLI · PUBLIC SITE VERIFICATION</div>
      <h1>复杂自然语言爬取<br>能力验证报告</h1>
      <p class="lead">覆盖多页翻页、无限滚动、列表进入详情、筛选后翻页与中断恢复。所有结果来自真实无头浏览器执行，并保留逐节点事件和原始 JSON 证据。</p>
      <span class="verdict {verdict_class}">{_escape(verdict)}</span>
    </div>
  </section>
  <main>
    <section class="summary">
      <div><b>{completed}/{len(runs)}</b><span>完成任务</span></div>
      <div><b>{average_score:.0f}</b><span>平均准确度</span></div>
      <div><b>{total_items}</b><span>唯一结果总数</span></div>
      <div><b>{total_seconds:.1f}s</b><span>真实浏览器总耗时</span></div>
    </section>
    <section class="panel">
      <h2>运行闭环</h2>
      <div class="flow">
        <span>自然语言</span><i>→</i><span>结构化任务契约</span><i>→</i>
        <span>Observer 感知</span><i>→</i><span>确定性 Planner</span><i>→</i>
        <span>dp-cli 执行</span><i>→</i><span>Verifier 验证</span><i>→</i>
        <span>持久化进度 / 完成</span>
      </div>
    </section>
    <section class="panel">
      <h2>五类任务总览</h2>
      <table>
        <thead><tr><th>#</th><th>能力与站点</th><th>结果数</th><th>耗时</th><th>得分</th><th>状态</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
    </section>
    {"".join(details)}
    <p class="muted">生成时间：{_escape(generated_at)} · 本报告不依赖外部资源，可离线打开。</p>
  </main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="Complex benchmark JSON files.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/reports/autoweb_complex_tasks_verified_20260721.html"),
    )
    args = parser.parse_args()

    runs = load_runs(args.inputs)
    missing = [key for key in CASE_ORDER if key not in {(run.get("case") or {}).get("key") for run in runs}]
    if missing:
        parser.error(f"missing benchmark cases: {', '.join(missing)}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_report(runs), encoding="utf-8")
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
