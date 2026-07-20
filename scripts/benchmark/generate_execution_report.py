from __future__ import annotations

import argparse
import html
import json
import statistics
import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_runs(paths: list[Path]) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for run in payload.get("runs") or []:
            if isinstance(run, dict):
                copied = dict(run)
                copied["_source_file"] = str(path.resolve())
                runs.append(copied)
    return runs


def _git_value(repo: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        return completed.stdout.strip() if completed.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _pct(numerator: int | float, denominator: int | float) -> str:
    if not denominator:
        return "0.0%"
    return f"{numerator / denominator * 100:.1f}%"


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _task_pass(run: dict[str, Any]) -> bool:
    checks = (run.get("evaluation") or {}).get("checks") or {}
    return bool(checks) and all(bool(value) for value in checks.values())


def _status_label(status: str) -> str:
    return {
        "completed": "自主完成",
        "max_resumes": "达到轮次上限",
        "exception": "异常中止",
        "stopped": "图停止但未完成",
    }.get(status, status or "unknown")


def _class_name(value: bool) -> str:
    return "pass" if value else "fail"


def _compact_exception(run: dict[str, Any]) -> str:
    exception = str(run.get("exception") or "").strip()
    if exception:
        return exception
    errors = []
    for result in run.get("results") or []:
        error = result.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            message = error.get("message")
            if code or message:
                errors.append(f"{code or 'error'}: {message or ''}".strip())
        elif error:
            errors.append(str(error))
    return "; ".join(dict.fromkeys(errors)) or "—"


def _evidence_counts(runs: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter()
    for run in runs:
        evaluation = run.get("evaluation") or {}
        case = run.get("case") or {}
        unique_count = int(evaluation.get("unique_item_count") or 0)
        expected_max = int(case.get("expected_max_items") or 0)
        if expected_max and unique_count > expected_max:
            counts["over_limit"] += 1
        if run.get("status") == "exception":
            counts["exceptions"] += 1
        for result in run.get("results") or []:
            error = result.get("error")
            if isinstance(error, dict) and error.get("code") == "ref_not_found":
                counts["ref_not_found"] += 1
            action = str(result.get("action") or "").lower()
            if action in {"extract", "list-items", "batch-detail-extract"}:
                counts["data_actions"] += 1
                if result.get("ok"):
                    counts["successful_data_actions"] += 1
        if evaluation.get("detail_batch_ran"):
            counts["detail_batch"] += 1
    return dict(counts)


def _site_rows(runs: list[dict[str, Any]]) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        grouped[str((run.get("case") or {}).get("key") or "unknown")].append(run)

    rows = []
    for key, site_runs in grouped.items():
        case = site_runs[0].get("case") or {}
        scores = [
            float((run.get("evaluation") or {}).get("accuracy_score") or 0)
            for run in site_runs
        ]
        opened = sum(
            bool(((run.get("evaluation") or {}).get("checks") or {}).get("target_opened"))
            for run in site_runs
        )
        count_pass = sum(
            bool(
                ((run.get("evaluation") or {}).get("checks") or {}).get(
                    "minimum_unique_items"
                )
            )
            for run in site_runs
        )
        field_pass = sum(
            bool(
                ((run.get("evaluation") or {}).get("checks") or {}).get(
                    "required_field_coverage_80pct"
                )
            )
            for run in site_runs
        )
        autonomous = sum(
            bool(
                ((run.get("evaluation") or {}).get("checks") or {}).get(
                    "autonomous_completion"
                )
            )
            for run in site_runs
        )
        passed = sum(_task_pass(run) for run in site_runs)
        avg_seconds = statistics.mean(
            float(run.get("elapsed_seconds") or 0) for run in site_runs
        )
        counts = [
            int((run.get("evaluation") or {}).get("unique_item_count") or 0)
            for run in site_runs
        ]
        url = _esc(case.get("url") or "")
        name = _esc(case.get("name") or key)
        rows.append(
            "<tr>"
            f'<td><strong>{name}</strong><br><a href="{url}">{url}</a></td>'
            f"<td>{len(site_runs)}</td>"
            f"<td>{statistics.mean(scores):.1f}%"
            f'<br><small>{min(scores):.1f}–{max(scores):.1f}%</small></td>'
            f"<td>{opened}/{len(site_runs)}</td>"
            f"<td>{count_pass}/{len(site_runs)}</td>"
            f"<td>{field_pass}/{len(site_runs)}</td>"
            f"<td>{autonomous}/{len(site_runs)}</td>"
            f'<td class="{_class_name(passed == len(site_runs))}">'
            f"{passed}/{len(site_runs)}</td>"
            f"<td>{avg_seconds:.1f}s</td>"
            f"<td>{_esc(', '.join(map(str, counts)))}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _run_rows(runs: list[dict[str, Any]]) -> str:
    rows = []
    occurrence: Counter[str] = Counter()
    for run in runs:
        case = run.get("case") or {}
        key = str(case.get("key") or "unknown")
        occurrence[key] += 1
        evaluation = run.get("evaluation") or {}
        checks = evaluation.get("checks") or {}
        passed_checks = sum(bool(value) for value in checks.values())
        data_count = int(evaluation.get("unique_item_count") or 0)
        expected = (
            f"{case.get('expected_min_items', '?')}"
            if case.get("expected_min_items") == case.get("expected_max_items")
            else f"{case.get('expected_min_items', '?')}–{case.get('expected_max_items', '?')}"
        )
        task_ok = _task_pass(run)
        rows.append(
            "<tr>"
            f"<td>{_esc(key)} #{occurrence[key]}</td>"
            f"<td>{_esc(_status_label(str(run.get('status') or '')))}</td>"
            f"<td>{float(evaluation.get('accuracy_score') or 0):.1f}% "
            f"<small>({passed_checks}/7)</small></td>"
            f"<td>{data_count}/{_esc(expected)}</td>"
            f'<td class="{_class_name(task_ok)}">{"PASS" if task_ok else "FAIL"}</td>'
            f"<td>{float(run.get('elapsed_seconds') or 0):.1f}s</td>"
            f"<td><code>{_esc(_compact_exception(run))}</code></td>"
            "</tr>"
        )
    return "\n".join(rows)


def _pilot_summary(paths: list[Path]) -> str:
    cards = []
    for path in paths:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        runs = payload.get("runs") or []
        if not runs and isinstance(payload, dict) and payload.get("case"):
            runs = [payload]
        for run in runs:
            evaluation = run.get("evaluation") or {}
            cards.append(
                '<div class="mini-card">'
                f"<strong>{_esc(path.stem)}</strong>"
                f"<span>状态：{_esc(_status_label(str(run.get('status') or '')))}</span>"
                f"<span>抽取：{int(evaluation.get('unique_item_count') or 0)} 条</span>"
                f"<span>detail batch：{'是' if evaluation.get('detail_batch_ran') else '否'}</span>"
                f"<span>耗时：{float(run.get('elapsed_seconds') or 0):.1f}s</span>"
                "</div>"
            )
    return "\n".join(cards) or '<p class="muted">未载入补充诊断样本。</p>'


def _metric_card(label: str, value: str, note: str, tone: str = "") -> str:
    return (
        f'<article class="metric {tone}">'
        f"<span>{_esc(label)}</span><strong>{_esc(value)}</strong>"
        f"<small>{_esc(note)}</small></article>"
    )


def build_report(
    runs: list[dict[str, Any]],
    source_paths: list[Path],
    pilot_paths: list[Path],
) -> str:
    total = len(runs)
    checks = [(run.get("evaluation") or {}).get("checks") or {} for run in runs]
    task_passes = sum(_task_pass(run) for run in runs)
    opened = sum(bool(item.get("target_opened")) for item in checks)
    count_passes = sum(bool(item.get("minimum_unique_items")) for item in checks)
    field_passes = sum(bool(item.get("required_field_coverage_80pct")) for item in checks)
    anchors = sum(bool(item.get("known_anchor_present")) for item in checks)
    completions = sum(bool(item.get("autonomous_completion")) for item in checks)
    exceptions = sum(run.get("status") == "exception" for run in runs)
    session_closes = sum(bool((run.get("session_close") or {}).get("ok")) for run in runs)
    scores = [
        float((run.get("evaluation") or {}).get("accuracy_score") or 0)
        for run in runs
    ]
    avg_score = statistics.mean(scores) if scores else 0
    latencies = [float(run.get("elapsed_seconds") or 0) for run in runs]
    median_latency = statistics.median(latencies) if latencies else 0
    evidence = _evidence_counts(runs)
    site_count = len(
        {
            str((run.get("case") or {}).get("key") or "")
            for run in runs
        }
    )

    autoweb_branch = _git_value(PROJECT_ROOT, "branch", "--show-current")
    autoweb_commit = _git_value(PROJECT_ROOT, "rev-parse", "--short", "HEAD")
    dpcli_root = PROJECT_ROOT.parent / "drissionpage-cli"
    dpcli_branch = _git_value(dpcli_root, "branch", "--show-current")
    dpcli_commit = _git_value(dpcli_root, "rev-parse", "--short", "HEAD")

    metrics = "".join(
        [
            _metric_card(
                "任务级通过率",
                _pct(task_passes, total),
                f"{task_passes}/{total} 次满足全部 7 项标准",
                "danger",
            ),
            _metric_card(
                "目标站打开率",
                _pct(opened, total),
                f"{opened}/{total} 次进入正确域名",
                "warn",
            ),
            _metric_card(
                "数量达标率",
                _pct(count_passes, total),
                f"{count_passes}/{total} 次达到最小条数",
                "danger",
            ),
            _metric_card(
                "字段达标率",
                _pct(field_passes, total),
                f"{field_passes}/{total} 次字段覆盖 ≥80%",
                "danger",
            ),
            _metric_card(
                "自主完成率",
                _pct(completions, total),
                f"{completions}/{total} 次无需轮次上限终止",
                "danger",
            ),
            _metric_card(
                "会话清理率",
                _pct(session_closes, total),
                f"{session_closes}/{total} 次浏览器会话成功关闭",
                "good",
            ),
        ]
    )

    raw_links = " · ".join(
        f'<a href="../benchmarks/{_esc(path.name)}">{_esc(path.name)}</a>'
        for path in source_paths
    )
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    template = Template(
        """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AutoWeb 自然语言爬取执行报告</title>
  <style>
    :root { --bg:#f5f7fb; --paper:#fff; --ink:#182230; --muted:#667085;
      --line:#e4e7ec; --brand:#3157d5; --brand-soft:#eef2ff;
      --good:#067647; --good-bg:#ecfdf3; --warn:#b54708; --warn-bg:#fffaeb;
      --danger:#b42318; --danger-bg:#fef3f2; --code:#101828; }
    * { box-sizing:border-box; }
    html { scroll-behavior:smooth; }
    body { margin:0; background:var(--bg); color:var(--ink);
      font:15px/1.68 Inter, "Segoe UI", "Microsoft YaHei", sans-serif; }
    a { color:var(--brand); text-decoration:none; }
    a:hover { text-decoration:underline; }
    code { color:var(--code); font-size:12px; overflow-wrap:anywhere; }
    .shell { width:min(1240px, calc(100% - 36px)); margin:0 auto 80px; }
    .hero { margin:28px 0 20px; padding:42px; color:white; border-radius:24px;
      background:linear-gradient(135deg,#172554 0%,#3157d5 58%,#58a6ff 130%);
      box-shadow:0 18px 48px rgba(29,49,112,.22); }
    .eyebrow { letter-spacing:.12em; text-transform:uppercase; font-size:12px;
      font-weight:700; opacity:.78; }
    h1 { margin:12px 0 10px; font-size:clamp(30px,5vw,52px); line-height:1.08; }
    .hero p { max-width:850px; margin:0; color:#e0e7ff; font-size:17px; }
    .hero-meta { display:flex; flex-wrap:wrap; gap:8px 20px; margin-top:24px;
      font-size:13px; color:#dbeafe; }
    .toc { display:flex; gap:8px; flex-wrap:wrap; padding:0 0 16px; }
    .toc a { padding:7px 12px; background:var(--paper); border:1px solid var(--line);
      border-radius:999px; font-size:13px; }
    .metrics { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }
    .metric { min-height:132px; padding:20px; border-radius:18px;
      background:var(--paper); border:1px solid var(--line); }
    .metric span,.metric small { display:block; color:var(--muted); }
    .metric strong { display:block; margin:6px 0 3px; font-size:31px; line-height:1.1; }
    .metric.danger { background:var(--danger-bg); border-color:#fecdca; }
    .metric.warn { background:var(--warn-bg); border-color:#fedf89; }
    .metric.good { background:var(--good-bg); border-color:#abefc6; }
    section { margin-top:18px; padding:30px; background:var(--paper);
      border:1px solid var(--line); border-radius:20px; }
    h2 { margin:0 0 14px; font-size:24px; line-height:1.25; }
    h3 { margin:24px 0 8px; font-size:17px; }
    p { margin:8px 0 12px; }
    .lead { font-size:17px; }
    .verdict { display:grid; grid-template-columns:170px 1fr; gap:24px;
      align-items:center; padding:22px; border-radius:16px;
      background:var(--danger-bg); border:1px solid #fecdca; }
    .grade { text-align:center; }
    .grade strong { display:block; font-size:58px; line-height:1; color:var(--danger); }
    .grade span { color:var(--danger); font-weight:700; }
    .callout { padding:15px 17px; border-left:4px solid var(--brand);
      background:var(--brand-soft); border-radius:8px; }
    .flow { display:flex; align-items:stretch; overflow-x:auto; gap:8px; padding:10px 0 4px; }
    .flow-card { flex:0 0 128px; padding:12px; border:1px solid #c7d7fe;
      border-radius:14px; background:#f5f8ff; }
    .flow-card strong { display:block; margin-bottom:5px; color:#243b8f; }
    .flow-card small { color:var(--muted); }
    .arrow { align-self:center; color:var(--brand); font-size:24px; font-weight:700; }
    .loop { margin:10px 0 0; padding:10px 14px; border-radius:10px;
      color:#344054; background:#f2f4f7; }
    .table-wrap { overflow:auto; border:1px solid var(--line); border-radius:14px; }
    table { width:100%; border-collapse:collapse; min-width:960px; }
    th,td { padding:11px 12px; text-align:left; vertical-align:top;
      border-bottom:1px solid var(--line); }
    th { position:sticky; top:0; background:#f8fafc; color:#475467;
      font-size:12px; text-transform:uppercase; letter-spacing:.03em; }
    tr:last-child td { border-bottom:0; }
    td.pass { color:var(--good); font-weight:700; }
    td.fail { color:var(--danger); font-weight:700; }
    small,.muted { color:var(--muted); }
    .issues { display:grid; grid-template-columns:repeat(2,1fr); gap:13px; }
    .issue { padding:18px; border:1px solid var(--line); border-radius:15px; }
    .issue .priority { display:inline-block; padding:2px 8px; margin-bottom:8px;
      border-radius:99px; color:white; background:var(--danger); font-size:11px; font-weight:800; }
    .issue.p1 .priority { background:var(--warn); }
    .issue strong { display:block; margin-bottom:5px; }
    .mini-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:10px; }
    .mini-card { display:flex; flex-direction:column; gap:2px; padding:14px;
      border:1px solid var(--line); border-radius:12px; }
    .methods { display:grid; grid-template-columns:1fr 1fr; gap:18px; }
    ul,ol { padding-left:20px; }
    li { margin:6px 0; }
    footer { padding:24px 8px; color:var(--muted); font-size:13px; }
    @media (max-width:800px) {
      .shell { width:min(100% - 20px,1240px); }
      .hero,section { padding:22px; border-radius:16px; }
      .metrics,.issues,.methods,.mini-grid { grid-template-columns:1fr; }
      .verdict { grid-template-columns:1fr; }
      .flow-card { flex-basis:155px; }
    }
    @media print {
      body { background:white; }
      .shell { width:100%; margin:0; }
      .hero { box-shadow:none; }
      section,.metric { break-inside:avoid; }
      .toc { display:none; }
    }
  </style>
</head>
<body>
<main class="shell">
  <header class="hero">
    <div class="eyebrow">AutoWeb V6 · Execution Audit</div>
    <h1>自然语言自动化爬取<br>执行准确性与稳定性报告</h1>
    <p>以 5 个公开、允许测试的爬虫沙箱站点，对真实 LangGraph + LLM + drissionpage-cli 闭环进行重复验证。报告把“能否打开网页”和“是否忠实完成用户任务”分开统计。</p>
    <div class="hero-meta">
      <span>生成时间：$generated</span><span>站点：$site_count</span>
      <span>独立任务：$total</span><span>AutoWeb：$autoweb_branch@$autoweb_commit</span>
      <span>drissionpage-cli：$dpcli_branch@$dpcli_commit</span>
    </div>
  </header>

  <nav class="toc">
    <a href="#summary">结论</a><a href="#architecture">Agent 流程</a>
    <a href="#matrix">站点矩阵</a><a href="#runs">逐次结果</a>
    <a href="#causes">根因</a><a href="#method">方法与证据</a>
  </nav>

  <div class="metrics">$metrics</div>

  <section id="summary">
    <h2>执行结论</h2>
    <div class="verdict">
      <div class="grade"><strong>D</strong><span>不具备无人值守生产条件</span></div>
      <div>
        <p class="lead"><strong>底层浏览器链路可运行，但自然语言任务闭环当前不准确、不稳定。</strong></p>
        <p>正确域名打开率为 <strong>$open_rate</strong>，会话清理率为 <strong>$close_rate</strong>；但完整任务通过率仅 <strong>$task_rate</strong>。平均检查项得分为 <strong>$avg_score%</strong>，中位耗时 <strong>$median_latency 秒</strong>。这不是“完全爬不到”，而是“动作可以执行，但经常执行错目标、错字段、错数量，并且不能自行收敛结束”。</p>
      </div>
    </div>
    <h3>最重要的判断</h3>
    <ul>
      <li>网页与浏览器层：多次成功打开站点、生成动态 DOM 快照、执行抽取，说明 dp_cli 会话和 DrissionPage 基础链路并非主要单点故障。</li>
      <li>语义到动作层：Planner 明确理解“5 条、正文/作者、球队字段”，但回退策略把动作改写为固定 <code>title + url + limit 20</code>，导致用户约束在执行前丢失。</li>
      <li>验收层：Verifier 只验证“生成动作自己的 schema 是否有值”，未对照原始任务的字段和条数，所以把分页链接、站点导航等错误数据判为成功。</li>
      <li>稳定性层：模型 API 连接异常 $exceptions 次；自主完成 $completions/$total 次。即使某一步成功，图仍会重复规划，直至达到测试轮次上限。</li>
    </ul>
  </section>

  <section id="architecture">
    <h2>当前项目如何从自然语言执行爬取</h2>
    <p>本次测试使用 <code>execution_mode=dp_cli</code>。Agent 并不是直接“看完整网页截图后写爬虫”，而是消费 dp_cli 生成的结构化、压缩 DOM 视图，再由多个节点循环决策。</p>
    <div class="flow" aria-label="AutoWeb execution flow">
      <div class="flow-card"><strong>1. 用户任务</strong><small>目标站、字段、数量、分页与结束条件</small></div>
      <div class="arrow">→</div>
      <div class="flow-card"><strong>2. Observer</strong><small>dp_cli snapshot；页面身份、元素 ref、数据区域、分页/表单能力</small></div>
      <div class="arrow">→</div>
      <div class="flow-card"><strong>3. Planner</strong><small>LLM 结合任务、压缩视图、历史步骤与验收结果决定下一意图</small></div>
      <div class="arrow">→</div>
      <div class="flow-card"><strong>4. Cache / Target</strong><small>尝试复用动作；必要时从候选 ref 中选择目标</small></div>
      <div class="arrow">→</div>
      <div class="flow-card"><strong>5. Coder</strong><small>把意图生成结构化 action JSON</small></div>
      <div class="arrow">→</div>
      <div class="flow-card"><strong>6. Executor</strong><small>受控调用 dp_cli open / click / extract / list-items</small></div>
      <div class="arrow">→</div>
      <div class="flow-card"><strong>7. Verifier</strong><small>检查 URL、动作返回、schema 覆盖并回写状态</small></div>
    </div>
    <div class="loop">↺ 未完成或验证失败时回到 Observer / Planner；Planner 给出 finish 且状态正确收敛时才结束。</div>
    <h3>Agent 实际“看到”的内容</h3>
    <div class="methods">
      <div class="callout"><strong>看到</strong><br>原始用户任务、当前 URL/标题、压缩后的可访问性/DOM 节点、稳定 ref、data_regions、分页/表单/导航能力、候选样本、finished_steps、上一步执行与校验结果。</div>
      <div class="callout"><strong>默认不直接看到</strong><br>完整原始 DOM、浏览器内部对象、所有隐藏节点；本次 dp_cli 模式也没有把整页视觉截图作为 Planner 的主要输入。压缩视图选错区域时，后续 LLM 很难自行纠正。</div>
    </div>
  </section>

  <section id="matrix">
    <h2>站点级准确性与稳定性</h2>
    <p>每个站点共执行 3 次独立自然语言任务。任务级 PASS 要求同时满足：正确站点、最小条数、最大条数、字段覆盖、已知锚点、未越权进入详情、图自主完成。</p>
    <div class="table-wrap"><table>
      <thead><tr><th>测试站点 / 能力</th><th>次数</th><th>平均检查分</th><th>打开</th><th>数量</th><th>字段</th><th>自主完成</th><th>任务 PASS</th><th>平均耗时</th><th>实际条数</th></tr></thead>
      <tbody>$site_rows</tbody>
    </table></div>
  </section>

  <section id="runs">
    <h2>全部独立运行</h2>
    <p>“检查分”用于展示部分能力，不等同任务成功。即使打开正确站点并正常关闭会话，只要字段、数量或结束条件不符合，任务仍判 FAIL。</p>
    <div class="table-wrap"><table>
      <thead><tr><th>场景</th><th>状态</th><th>检查分</th><th>实际/期望</th><th>任务</th><th>耗时</th><th>异常或动作错误</th></tr></thead>
      <tbody>$run_rows</tbody>
    </table></div>
  </section>

  <section id="causes">
    <h2>不稳定与定位不准确的具体原因</h2>
    <div class="callout"><strong>责任边界结论：不能把失败全部归因于 drissionpage-cli。</strong><br>
      当前最致命的字段/数量丢失、错误成功判定和无法结束，代码证据位于 AutoWeb；虚拟 ref 与 wait 的问题属于 AutoWeb↔dp_cli 接口合同；dp_cli 自身仍需改进数据区域的语义投影，因为它在 Quotes JS / Hockey 的候选区域中返回了分页或站点导航；另有独立的模型 API 波动。</div>
    <div class="issues">
      <article class="issue"><span class="priority">P0</span><strong>用户 schema 与 limit 在回退改写中丢失</strong>
        <p>Planner 已输出正确字段/数量，但 <code>_dpcli_recoverable_data_candidate()</code> 对 extract 使用固定 <code>schema=["title","url"], limit=20</code>。Books 因此从 5 条变 20 条；Quotes/Hockey 被强制成不相关字段。</p>
        <small>代码证据：core/nodes/_dpcli.py 的 recoverable candidate 构造。</small></article>
      <article class="issue"><span class="priority">P0</span><strong>索引层 group_ref 与运行时 ref 命名空间不一致</strong>
        <p>Quotes 静态页把 <code>g_pagination_*</code> 规划组直接传给 dp_cli <code>list-items</code>，运行时会话并不知道该 ref，产生 <code>ref_not_found</code>，重复 snapshot 后仍选择同一不可执行引用。</p>
        <small>本组观测到 ref_not_found 动作 $ref_not_found 次。</small></article>
      <article class="issue"><span class="priority">P0</span><strong>Verifier 校验“动作 schema”，没有校验“用户任务”</strong>
        <p>Quotes JS 把 “Next →” 分页链接当 1 条名言；Hockey 把 Sandbox/Lessons/FAQ 导航当球队数据。因为这些结果满足错误动作的 title/url schema，Verifier 报 100% coverage。</p>
        <small>代码证据：core/nodes/verifier.py 的 schema_match 分支缺少任务字段与最小条数对照。</small></article>
      <article class="issue"><span class="priority">P0</span><strong>结束条件没有形成闭环</strong>
        <p>所有 $total 次任务中自主完成仅 $completions 次。成功动作之后继续 Observer → Planner，重复抽取或重新导航，最终由测试器的 max-resumes 停止，而不是 Agent 自己确认任务完成。</p></article>
      <article class="issue"><span class="priority">P0</span><strong>Planner 与 Executor 的动作词表不一致</strong>
        <p>Quotes JS 复测时 Planner 正确意识到需要等待动态内容，连续生成 <code>wait</code>；但 <code>DPCLIExecutor.execute_action()</code> 没有 wait 分支，返回 unsupported action。图随后直接重试 Coder，没有产生新的页面观察。</p>
        <small>本组动作统计中 wait 失败 2 次，且未形成可恢复等待。</small></article>
      <article class="issue p1"><span class="priority">P1</span><strong>外部模型调用存在连接波动</strong>
        <p>共 $exceptions 次运行以异常中止，代表即使浏览器会话正常，也可能在 Planner/Coder 调用模型时断链。目前缺少覆盖规划节点的退避重试、熔断和可恢复续跑。</p></article>
      <article class="issue p1"><span class="priority">P1</span><strong>详情意图采用关键词正匹配，无法理解否定</strong>
        <p>补充 Books 诊断中，“详情链接”或“不要进入详情页”仍触发批量详情提取；策略既未区分“要 URL”与“要详情字段”，也未优先识别否定表达。</p>
        <small>代码证据：skills/dpcli_crawl_policy.py 的 goal_requests_detail_batch()。</small></article>
    </div>
    <h3>补充诊断样本（不计入 15 次主矩阵）</h3>
    <div class="mini-grid">$pilot_summary</div>
  </section>

  <section id="recommendations">
    <h2>修复优先级与验收门槛</h2>
    <ol>
      <li><strong>P0 — 建立 TaskContract：</strong>从用户任务提取 required_fields、min/max_items、pages、detail_policy；任何 Planner rewrite、Coder action、Verifier 结果都必须携带并对照同一合同，禁止用固定 schema/limit 覆盖。</li>
      <li><strong>P0 — 统一 ref 合同：</strong>索引虚拟组使用独立字段，不得直接进入 Executor；执行前调用 resolve/映射为当前 session 可识别的 r*/e* ref，ref_not_found 后排除失败候选。</li>
      <li><strong>P0 — 任务级验收：</strong>Verifier 增加条数上下限、所需字段别名、锚点/区域语义、分页累计和重复率检查；动作返回 ok 只代表命令成功，不代表任务成功。</li>
      <li><strong>P0 — 确定性终止：</strong>合同满足后直接 finish；连续相同 action / 相同结果时进入恢复分支，不能继续重复抽取。</li>
      <li><strong>P1 — API 韧性：</strong>为 Planner/Coder/Verifier 加指数退避、限次重试、节点级 checkpoint 和错误分类；报告 API 可用率。</li>
      <li><strong>P1 — 回归门槛：</strong>同一 5 站矩阵连续 3 轮（15/15）任务级通过、字段覆盖 ≥95%、数量约束 100%、自主完成 ≥95%，再考虑无人值守运行。</li>
    </ol>
  </section>

  <section id="method">
    <h2>测试方法、边界与可复现证据</h2>
    <div class="methods">
      <div>
        <h3>执行配置</h3>
        <ul>
          <li>真实 AutoWeb LangGraph，真实配置模型，真实 drissionpage-cli 子进程。</li>
          <li>无头浏览器；每次独立 session；HITL 自动放行，仅用于无人值守基准。</li>
          <li>每次最多 4 次 interrupt-resume；超出即记 max_resumes，不伪装为完成。</li>
          <li>$site_count 个场景 × 3 次，共 $total 次；无并发抓取。</li>
          <li>平均检查项得分 $avg_score%；已知内容锚点命中率 $anchor_rate。</li>
        </ul>
      </div>
      <div>
        <h3>公开站与合规范围</h3>
        <ul>
          <li><a href="https://toscrape.com/">ToScrape</a> 明确提供 Books / Quotes 作为爬虫测试沙箱。</li>
          <li><a href="https://web-scraping.dev/">web-scraping.dev</a> 是面向抓取练习的测试站；目标路径未被 robots 禁止。</li>
          <li><a href="https://www.scrapethissite.com/">Scrape This Site</a> 是公开练习站；未访问其 robots 禁止的 lessons 路径。</li>
          <li>不登录、不提交表单、不绕过验证码、不抓取个人或敏感数据。</li>
        </ul>
      </div>
    </div>
    <h3>评分定义</h3>
    <p><code>accuracy_score = 通过检查数 / 7 × 100%</code>。七项为：目标域名、最小条数、最大条数、所需字段覆盖 ≥80%、已知锚点、没有未请求详情批处理、图自主完成。任务 PASS 必须 7/7；该透明评分用于定位能力缺口，不宣称是通用模型准确率。</p>
    <h3>原始证据</h3>
    <p>$raw_links</p>
    <p class="muted">所有运行都保存 action、dpcli_result、错误码、样本数据、耗时和 session_close 结果。报告未把 pilot 诊断样本计入主矩阵，也未用人工修正结果。</p>
  </section>

  <footer>
    结论基于当前本地工作树，而非干净发布版本。AutoWeb <code>$autoweb_branch@$autoweb_commit</code>；
    drissionpage-cli <code>$dpcli_branch@$dpcli_commit</code>。生成于 $generated。
  </footer>
</main>
</body>
</html>"""
    )

    return template.substitute(
        generated=_esc(generated),
        site_count=site_count,
        total=total,
        autoweb_branch=_esc(autoweb_branch),
        autoweb_commit=_esc(autoweb_commit),
        dpcli_branch=_esc(dpcli_branch),
        dpcli_commit=_esc(dpcli_commit),
        metrics=metrics,
        open_rate=_pct(opened, total),
        close_rate=_pct(session_closes, total),
        task_rate=_pct(task_passes, total),
        avg_score=f"{avg_score:.1f}",
        median_latency=f"{median_latency:.1f}",
        exceptions=exceptions,
        completions=completions,
        anchor_rate=_pct(anchors, total),
        site_rows=_site_rows(runs),
        run_rows=_run_rows(runs),
        ref_not_found=evidence.get("ref_not_found", 0),
        pilot_summary=_pilot_summary(pilot_paths),
        raw_links=raw_links,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the AutoWeb HTML benchmark report.")
    parser.add_argument("inputs", nargs="+", help="Benchmark JSON files.")
    parser.add_argument(
        "--output",
        default="output/reports/autoweb_execution_report_20260719.html",
    )
    parser.add_argument(
        "--pilots",
        default="output/benchmarks/pilot_books.json,output/benchmarks/pilot_books_2.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    inputs = [Path(item).resolve() for item in args.inputs]
    missing = [str(path) for path in inputs if not path.exists()]
    if missing:
        raise SystemExit(f"Missing benchmark files: {', '.join(missing)}")
    runs = _load_runs(inputs)
    if not runs:
        raise SystemExit("No benchmark runs found.")

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    pilot_paths = [
        Path(item.strip()).resolve()
        for item in str(args.pilots).split(",")
        if item.strip()
    ]
    report = build_report(runs, inputs, pilot_paths)
    output.write_text(report, encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "runs": len(runs),
                "sites": len(
                    {
                        str((run.get("case") or {}).get("key") or "")
                        for run in runs
                    }
                ),
                "bytes": output.stat().st_size,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
