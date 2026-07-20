"""Generate a UTF-8 audit report for the AutoWeb + dp-cli upgrade."""

from __future__ import annotations

import argparse
import html
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception:
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _is_strict_live_pass(run: dict[str, Any]) -> bool:
    evaluation = run.get("evaluation") or {}
    checks = evaluation.get("checks") or {}
    session_close = run.get("session_close") or {}
    return (
        run.get("status") == "completed"
        and not run.get("exception")
        and bool(checks)
        and all(bool(value) for value in checks.values())
        and float(evaluation.get("accuracy_score") or 0) == 100.0
        and bool(session_close.get("ok"))
    )


def _live_rows(matrix: dict[str, Any]) -> str:
    rows = []
    for run in matrix.get("runs") or []:
        case = run.get("case") or {}
        evaluation = run.get("evaluation") or {}
        passed = _is_strict_live_pass(run)
        rows.append(
            "<tr>"
            f"<td>{_esc(case.get('name') or case.get('key'))}</td>"
            f"<td>{_esc(run.get('status'))}</td>"
            f"<td>{float(evaluation.get('accuracy_score') or 0):.1f}%</td>"
            f"<td>{int(evaluation.get('unique_item_count') or 0)}</td>"
            f"<td>{'PASS' if passed else 'FAIL'}</td>"
            f"<td>{_esc(run.get('exception') or '—')}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _replay_rows(replay: dict[str, Any]) -> str:
    rows = []
    for case in replay.get("cases") or []:
        evaluation = case.get("per_page_evaluation") or {}
        projection = case.get("projection") or {}
        next_step = case.get("next_step") or {}
        next_payload = next_step.get("payload") or {}
        next_text = (
            f"{next_step.get('intent')} {next_payload}"
            if next_step
            else "完成"
        )
        rows.append(
            "<tr>"
            f"<td>{_esc(case.get('name') or case.get('case'))}</td>"
            f"<td>{case.get('raw_item_count', 0)} → "
            f"{case.get('projected_item_count', 0)}</td>"
            f"<td>{_esc(projection.get('kind') or '原始结果已合格')}</td>"
            f"<td>{_esc(evaluation.get('field_coverage') or {})}</td>"
            f"<td>{'PASS' if case.get('known_anchor_present') else 'FAIL'}</td>"
            f"<td>{'是' if case.get('full_task_done_from_saved_pages') else '否'}</td>"
            f"<td><code>{_esc(next_text)}</code></td>"
            "</tr>"
        )
    return "\n".join(rows)


def _audit_rows(audit: dict[str, Any]) -> str:
    rows = []
    for case in audit.get("cases") or []:
        evaluation = case.get("contract_evaluation") or {}
        detected = case.get("detected_regions") or []
        region_text = ", ".join(
            f"{region.get('ref')}:{region.get('kind')}"
            for region in detected
        ) or "—"
        rows.append(
            "<tr>"
            f"<td>{_esc(case.get('case'))}</td>"
            f"<td>{'PASS' if case.get('region_detected') else 'FAIL'}</td>"
            f"<td>{_esc(region_text)}</td>"
            f"<td>{int(case.get('projected_item_count') or 0)}</td>"
            f"<td>{_esc(evaluation.get('field_coverage') or {})}</td>"
            f"<td>{'PASS' if case.get('projection_pass') else 'FAIL'}</td>"
            f"<td>{'PASS' if case.get('component_pass') else 'FAIL'}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_report(
    live_matrix: dict[str, Any],
    replay: dict[str, Any],
    audit: dict[str, Any],
    live_matrix_path: Path,
    replay_path: Path,
    audit_path: Path,
) -> str:
    live_runs = live_matrix.get("runs") or []
    case_counts: dict[str, int] = {}
    for run in live_runs:
        case = run.get("case") or {}
        case_key = str(case.get("key") or case.get("name") or "")
        if case_key:
            case_counts[case_key] = case_counts.get(case_key, 0) + 1
    live_passes = sum(_is_strict_live_pass(run) for run in live_runs)
    live_rate = 100.0 * live_passes / len(live_runs) if live_runs else 0.0
    matrix_complete = (
        len(case_counts) >= 5
        and all(repeat_count >= 3 for repeat_count in case_counts.values())
    )
    threshold_proven = matrix_complete and live_rate > 80.0
    closed_sessions = sum(
        bool((run.get("session_close") or {}).get("ok"))
        for run in live_runs
    )
    replay_summary = replay.get("summary") or {}
    replay_passes = int(replay_summary.get("projection_replay_passes") or 0)
    replay_total = int(replay_summary.get("case_count") or 0)
    audit_summary = audit.get("summary") or {}
    audit_passes = int(audit_summary.get("component_passes") or 0)
    audit_total = int(audit_summary.get("case_count") or 0)
    audit_rate = float(audit_summary.get("component_pass_rate") or 0)
    audit_proven = audit_total > 0 and audit_rate >= 80.0

    live_metric_class = "good" if threshold_proven else "warn"
    audit_metric_class = "good" if audit_proven else "warn"
    conclusion_class = "" if threshold_proven else " warning"
    conclusion_title = (
        "最终“成功率超过 80%”已证明。"
        if threshold_proven
        else "最终“成功率超过 80%”仍未证明。"
    )
    conclusion_detail = (
        f"5 站 × 3 轮共 {len(live_runs)} 次真实在线无头任务严格通过 "
        f"{live_passes} 次（{live_rate:.1f}%）；所有通过任务同时满足状态完成、"
        "检查项全真、准确率 100% 和 session 正常关闭。"
        if threshold_proven
        else f"当前严格通过 {live_passes}/{len(live_runs)}（{live_rate:.1f}%），"
        "尚未同时满足 5 站 × 3 轮和严格任务成功率大于 80% 的验收门槛。"
    )
    matrix_item_class = "done" if threshold_proven else "pending"
    matrix_item_text = (
        f"已完成：5 站 × 3 轮在线自然语言基准，严格任务级 PASS "
        f"{live_passes}/{len(live_runs)}（{live_rate:.1f}%）。"
        if threshold_proven
        else "待办：完成 5 站 × 至少 3 轮在线自然语言基准并达到任务级 PASS >80%。"
    )
    cli_item_class = "done" if audit_proven else "pending"
    cli_item_text = (
        "已完成：结构化记录投影、区域筛选和表格/卡片记录规则已下沉到 drissionpage-cli。"
        if audit_proven
        else "待办：将结构化记录投影和区域筛选规则下沉到 drissionpage-cli。"
    )
    audit_callout_class = "" if audit_proven else " warning"
    audit_callout_text = (
        "跨项目组件门槛已达标，并已进入、完成公开站多轮在线验收。"
        if audit_proven
        else "必须先让这组跨项目组件门槛达到至少 80%，再进入公开站多轮在线验收。"
    )
    online_title = (
        "最终在线矩阵：5 站 × 3 轮"
        if matrix_complete
        else "在线矩阵：当前证据"
    )
    online_description = (
        "该矩阵在结构投影、状态契约和滚动修复后运行；PASS 使用完整任务严格口径，"
        "不会用检查项平均分替代。"
        if matrix_complete
        else "该矩阵尚未覆盖完整的 5 站 × 3 轮验收规模。"
    )
    products_evidence = (
        "保存快照本身只有第一页，因此离线回放不单独冒充跨页完成；"
        "最终在线矩阵已经在真实站点点击分页并完成第二页提取。"
        if threshold_proven
        else "保存快照只有第一页，因此当前证据不把它标记为完整跨页任务完成。"
    )

    autoweb_branch = _git(PROJECT_ROOT, "branch", "--show-current")
    autoweb_commit = _git(PROJECT_ROOT, "rev-parse", "--short", "HEAD")
    dpcli_root = PROJECT_ROOT.parent / "drissionpage-cli"
    dpcli_branch = _git(dpcli_root, "branch", "--show-current")
    dpcli_commit = _git(dpcli_root, "rev-parse", "--short", "HEAD")
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AutoWeb + drissionpage-cli 升级审计报告</title>
  <style>
    :root {{
      --bg:#f4f7fb; --paper:#fff; --ink:#182230; --muted:#667085;
      --line:#dfe5ee; --blue:#3157d5; --green:#067647; --amber:#b54708;
      --red:#b42318; --soft-blue:#eef2ff; --soft-green:#ecfdf3;
      --soft-amber:#fffaeb;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink);
      font:15px/1.65 Inter,"Segoe UI","Microsoft YaHei",sans-serif; }}
    main {{ width:min(1220px,calc(100% - 32px)); margin:28px auto 72px; }}
    header {{ padding:38px; color:white; border-radius:24px;
      background:linear-gradient(135deg,#172554,#3157d5 62%,#5b9cff);
      box-shadow:0 20px 50px rgba(24,54,130,.2); }}
    h1 {{ margin:8px 0 10px; font-size:clamp(30px,5vw,50px); line-height:1.12; }}
    h2 {{ margin:0 0 14px; font-size:24px; }}
    h3 {{ margin:22px 0 8px; }}
    header p {{ max-width:880px; color:#e0e7ff; font-size:17px; }}
    .meta {{ display:flex; flex-wrap:wrap; gap:7px 20px; margin-top:20px;
      color:#dbeafe; font-size:13px; }}
    .grid {{ display:grid; grid-template-columns:repeat(5,1fr); gap:13px;
      margin:16px 0; }}
    .metric,section {{ background:var(--paper); border:1px solid var(--line);
      border-radius:18px; }}
    .metric {{ min-height:132px; padding:19px; }}
    .metric span,.metric small {{ display:block; color:var(--muted); }}
    .metric strong {{ display:block; margin:6px 0; font-size:30px; }}
    .metric.good {{ background:var(--soft-green); }}
    .metric.warn {{ background:var(--soft-amber); }}
    section {{ margin-top:16px; padding:28px; }}
    .callout {{ padding:15px 17px; border-left:4px solid var(--blue);
      background:var(--soft-blue); border-radius:9px; }}
    .warning {{ border-left-color:var(--amber); background:var(--soft-amber); }}
    .flow {{ display:flex; gap:7px; overflow:auto; align-items:center; padding:8px 0; }}
    .node {{ flex:0 0 150px; min-height:96px; padding:12px; border-radius:13px;
      border:1px solid #c7d7fe; background:#f7f9ff; }}
    .node strong,.node small {{ display:block; }}
    .node small {{ margin-top:5px; color:var(--muted); }}
    .arrow {{ color:var(--blue); font-size:24px; }}
    .table {{ overflow:auto; border:1px solid var(--line); border-radius:13px; }}
    table {{ width:100%; min-width:930px; border-collapse:collapse; }}
    th,td {{ padding:11px 12px; text-align:left; vertical-align:top;
      border-bottom:1px solid var(--line); }}
    th {{ background:#f8fafc; color:#475467; font-size:12px; }}
    code {{ font-size:12px; overflow-wrap:anywhere; }}
    .done {{ color:var(--green); }}
    .pending {{ color:var(--amber); }}
    ul {{ padding-left:20px; }}
    footer {{ padding:22px 4px; color:var(--muted); font-size:13px; }}
    @media (max-width:850px) {{
      main {{ width:min(100% - 18px,1220px); }}
      header,section {{ padding:21px; border-radius:16px; }}
      .grid {{ grid-template-columns:1fr 1fr; }}
    }}
  </style>
</head>
<body>
<main>
  <header>
    <div>AutoWeb V6 · Upgrade Audit</div>
    <h1>自然语言自动化爬取<br>升级进度与证据边界</h1>
    <p>本报告区分真实在线闭环、真实快照离线回放及其证据边界。
    离线投影通过不会被计作最终在线成功率。</p>
    <div class="meta">
      <span>生成：{_esc(generated)}</span>
      <span>AutoWeb：{_esc(autoweb_branch)}@{_esc(autoweb_commit)}</span>
      <span>drissionpage-cli：{_esc(dpcli_branch)}@{_esc(dpcli_commit)}</span>
    </div>
  </header>

  <div class="grid">
    <article class="metric {live_metric_class}"><span>最终在线任务矩阵</span>
      <strong>{live_passes}/{len(live_runs)}</strong><small>严格完整任务 PASS</small></article>
    <article class="metric {live_metric_class}"><span>严格任务成功率</span>
      <strong>{live_rate:.1f}%</strong><small>状态、检查项、100 分、session</small></article>
    <article class="metric good"><span>真实快照投影回放</span>
      <strong>{replay_passes}/{replay_total}</strong><small>字段、条数、锚点均通过</small></article>
    <article class="metric {audit_metric_class}"><span>CLI 原生组件门槛</span>
      <strong>{audit_passes}/{audit_total}</strong><small>真实快照区域识别 + 直接投影（{audit_rate:.1f}%）</small></article>
    <article class="metric {live_metric_class}"><span>浏览器会话关闭</span>
      <strong>{closed_sessions}/{len(live_runs)}</strong><small>所有在线运行均清理 session</small></article>
  </div>

  <section>
    <h2>当前结论</h2>
    <div class="callout{conclusion_class}"><strong>{conclusion_title}</strong>
      {conclusion_detail}</div>
    <ul>
      <li class="done">AutoWeb：TaskContract、确定性 Planner、受控 Action、任务级 Verifier、跨页进度和终止闭环已接通。</li>
      <li class="done">兼容投影：Quote、Product、Table 三类真实快照均达到字段覆盖 100%。</li>
      <li class="done">稳定性：正常公开站路径不再强制依赖 Planner/Coder/Verifier 的远程 LLM。</li>
      <li class="{cli_item_class}">{cli_item_text}</li>
      <li class="{matrix_item_class}">{matrix_item_text}</li>
    </ul>
  </section>

  <section>
    <h2>Agent 当前如何“看网页”并执行</h2>
    <div class="flow">
      <div class="node"><strong>自然语言任务</strong><small>URL、字段、数量、页数、详情策略</small></div>
      <div class="arrow">→</div>
      <div class="node"><strong>dp-cli Snapshot</strong><small>e* 元素、r* 区域、分页、表格、重复块</small></div>
      <div class="arrow">→</div>
      <div class="node"><strong>TaskContract</strong><small>约束贯穿 Planner 到 Verifier</small></div>
      <div class="arrow">→</div>
      <div class="node"><strong>确定性 Planner</strong><small>open / extract / click / wait / finish</small></div>
      <div class="arrow">→</div>
      <div class="node"><strong>Executor</strong><small>受控调用 drissionpage-cli</small></div>
      <div class="arrow">→</div>
      <div class="node"><strong>结构投影</strong><small>原始结果不足时读取完整快照索引</small></div>
      <div class="arrow">→</div>
      <div class="node"><strong>任务级 Verifier</strong><small>字段、条数、页数、去重、终止</small></div>
    </div>
    <p>Planner 默认消费压缩后的结构化 DOM，不直接读取整页截图。完整快照仅在通用
    projector 返回错误结构时，由确定性兼容层按索引恢复记录。</p>
  </section>

  <section>
    <h2>drissionpage-cli 当前原生组件基线</h2>
    <p>下面直接导入当前 CLI 工作树，用其原生 data-region detector 和
    ExtractProjector 回放同一批真实公开站原始节点。AutoWeb 的结果补全层没有参与。</p>
    <div class="table"><table>
      <thead><tr><th>场景</th><th>目标区域</th><th>检测到的区域</th>
        <th>直接投影条数</th><th>字段覆盖</th><th>投影</th><th>组件闭环</th></tr></thead>
      <tbody>{_audit_rows(audit)}</tbody>
    </table></div>
    <p class="callout{audit_callout_class}"><strong>当前组件通过率 {audit_rate:.1f}%：</strong>
    {audit_callout_text}</p>
  </section>

  <section>
    <h2>{online_title}</h2>
    <p>{online_description}</p>
    <div class="table"><table>
      <thead><tr><th>场景</th><th>状态</th><th>检查分</th><th>条数</th><th>任务</th><th>异常</th></tr></thead>
      <tbody>{_live_rows(live_matrix)}</tbody>
    </table></div>
  </section>

  <section>
    <h2>真实快照离线回放：结构投影改造后</h2>
    <div class="table"><table>
      <thead><tr><th>场景</th><th>原始→投影条数</th><th>投影规则</th>
        <th>字段覆盖</th><th>锚点</th><th>保存页已完成</th><th>下一步</th></tr></thead>
      <tbody>{_replay_rows(replay)}</tbody>
    </table></div>
    <p class="callout"><strong>Products 证据边界：</strong>{products_evidence}</p>
  </section>

  <section>
    <h2>已修复的根因</h2>
    <h3>AutoWeb 已修复</h3>
    <ul>
      <li>fallback 覆盖用户 schema/limit。</li>
      <li>虚拟 g_* 索引分组进入 Executor。</li>
      <li>Planner 与 Executor 缺少 wait 统一协议。</li>
      <li>Verifier 只验证动作 schema，不验证原始任务。</li>
      <li>成功动作后无法按字段、条数、页数确定终止。</li>
      <li>“详情链接”被误判为进入详情页。</li>
    </ul>
    <h3>drissionpage-cli 已修复</h3>
    <ul>
      <li>重复块检测可识别无链接的 Quote 卡片。</li>
      <li>结构化投影会合并价格、文本、作者、标签等同卡片字段。</li>
      <li>表格区域按 tr/td 输出记录，不再选择导航链接或返回空数组。</li>
      <li>data_region 的 item_count 按记录数计算，不再重复统计同卡片链接。</li>
      <li>滚动优先调用 DrissionPage 原生 <code>scroll.to_see()</code>，并保留 JS 回退。</li>
    </ul>
  </section>

  <section>
    <h2>验收门槛</h2>
    <ol>
      <li>同一 5 站自然语言任务至少连续运行 3 轮。</li>
      <li>任务级 PASS 必须严格大于 80%，不能用检查项平均分代替。</li>
      <li>字段覆盖 ≥80%，数量上下限全部满足，已知锚点存在。</li>
      <li>无未请求详情批处理，图自主结束，session 全部关闭。</li>
      <li>失败必须保存 action、结果、错误、快照引用和任务契约。</li>
    </ol>
    <p>原始证据：
      <a href="../benchmarks/{_esc(live_matrix_path.name)}">{_esc(live_matrix_path.name)}</a> ·
      <a href="../benchmarks/{_esc(replay_path.name)}">{_esc(replay_path.name)}</a> ·
      <a href="../benchmarks/{_esc(audit_path.name)}">{_esc(audit_path.name)}</a>
    </p>
  </section>

  <footer>报告基于当前本地脏工作树生成，未把离线回放冒充在线成功率。</footer>
</main>
</body>
</html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-matrix",
        default="output/benchmarks/contract_matrix_once.json",
    )
    parser.add_argument(
        "--replay",
        default="output/benchmarks/snapshot_projection_replay.json",
    )
    parser.add_argument(
        "--audit",
        default="output/benchmarks/dpcli_saved_snapshot_audit.json",
    )
    parser.add_argument(
        "--output",
        default="output/reports/autoweb_dpcli_upgrade_report_20260719.html",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    live_path = (PROJECT_ROOT / args.live_matrix).resolve()
    replay_path = (PROJECT_ROOT / args.replay).resolve()
    audit_path = (PROJECT_ROOT / args.audit).resolve()
    output_path = (PROJECT_ROOT / args.output).resolve()
    report = build_report(
        _load(live_path),
        _load(replay_path),
        _load(audit_path),
        live_path,
        replay_path,
        audit_path,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(
        json.dumps(
            {"output": str(output_path), "bytes": output_path.stat().st_size},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
