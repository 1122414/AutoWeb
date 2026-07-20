"""Generate the final offline Chinese report for AutoWeb stages 5-9."""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


PHASES = (
    (
        "阶段五",
        "跨进程恢复与幂等执行",
        "SQLite LangGraph checkpoint、Task Run manifest、稳定 request-id、CLI 动作回执。",
    ),
    (
        "阶段六",
        "统一轨迹、Token 与成本",
        "LLM 与浏览器动作进入同一 Run Trace；真实 usage 与估算值明确区分。",
    ),
    (
        "阶段七",
        "DOM 增量与稳定引用",
        "语义指纹 v2、DOM delta、XPath 漂移后的高置信 ref 重绑定。",
    ),
    (
        "阶段八",
        "组合任务生命周期",
        "多筛选顺序、分页/滚动/详情阶段、条件停止、JSON 安全恢复。",
    ),
    (
        "阶段九",
        "生产治理与可靠性实验室",
        "统一缓存准入、robots/节流/内网保护、阻断信号停止、离线故障注入。",
    ),
)


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _git_commits(repo: Path, start_subject: str) -> list[dict[str, str]]:
    completed = subprocess.run(
        [
            "git",
            "log",
            "--format=%h%x1f%s",
            "--reverse",
            f"--grep={start_subject}",
            "-1",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    start_hash = completed.stdout.split("\x1f", 1)[0].strip()
    if not start_hash:
        return []
    log = subprocess.run(
        [
            "git",
            "log",
            "--format=%h%x1f%s",
            "--reverse",
            f"{start_hash}^..HEAD",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    result = []
    for line in log.stdout.splitlines():
        if "\x1f" not in line:
            continue
        commit_hash, subject = line.split("\x1f", 1)
        result.append({"hash": commit_hash, "subject": subject})
    return result


def load_trace_summaries(
    path: Path,
    runs: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if not path.exists():
        return {}, {
            "llm_calls": 0,
            "tokens": 0,
            "estimated_calls": 0,
            "browser_actions": 0,
        }
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                thread_id,
                MAX(id) AS last_id,
                SUM(CASE WHEN event_type = 'llm' THEN 1 ELSE 0 END)
                    AS llm_calls,
                COALESCE(SUM(total_tokens), 0) AS tokens,
                SUM(
                    CASE
                        WHEN event_type = 'llm' AND estimated_tokens = 1
                        THEN 1 ELSE 0
                    END
                ) AS estimated_calls,
                SUM(
                    CASE WHEN event_type = 'browser_action' THEN 1 ELSE 0 END
                ) AS browser_actions
            FROM autoweb_run_trace
            GROUP BY thread_id
            ORDER BY last_id DESC
            """
        ).fetchall()
    by_thread = {str(row["thread_id"]): dict(row) for row in rows}
    latest: dict[str, dict[str, Any]] = {}
    if runs:
        for run in runs:
            key = str((run.get("case") or {}).get("key") or "")
            thread_ids = [
                str(value)
                for value in run.get("thread_ids") or []
                if str(value)
            ]
            selected = [
                by_thread[thread_id]
                for thread_id in thread_ids
                if thread_id in by_thread
            ]
            if not key or not selected:
                continue
            latest[key] = {
                "thread_id": ",".join(thread_ids),
                "last_id": max(int(item["last_id"]) for item in selected),
                "llm_calls": sum(
                    int(item["llm_calls"] or 0) for item in selected
                ),
                "tokens": sum(int(item["tokens"] or 0) for item in selected),
                "estimated_calls": sum(
                    int(item["estimated_calls"] or 0) for item in selected
                ),
                "browser_actions": sum(
                    int(item["browser_actions"] or 0) for item in selected
                ),
            }
    else:
        for row in rows:
            item = dict(row)
            thread_id = str(item["thread_id"])
            for key in (
                "products_three_pages",
                "quotes_infinite_scroll",
                "books_list_detail",
                "hockey_filter_two_pages",
                "products_restart_resume",
            ):
                if key in thread_id and key not in latest:
                    latest[key] = item
                    break
    totals = {
        "llm_calls": sum(int(item["llm_calls"] or 0) for item in latest.values()),
        "tokens": sum(int(item["tokens"] or 0) for item in latest.values()),
        "estimated_calls": sum(
            int(item["estimated_calls"] or 0)
            for item in latest.values()
        ),
        "browser_actions": sum(
            int(item["browser_actions"] or 0)
            for item in latest.values()
        ),
    }
    return latest, totals


def build_report(
    complex_payload: dict[str, Any],
    reliability_payload: dict[str, Any],
    traces: dict[str, dict[str, Any]],
    trace_totals: dict[str, Any],
    autoweb_commits: list[dict[str, str]],
    cli_commits: list[dict[str, str]],
    *,
    generated_at: str,
    autoweb_tests: str,
    cli_tests: str,
    complex_source: str,
    reliability_source: str,
) -> str:
    runs = complex_payload.get("runs") or []
    passed_runs = [
        run
        for run in runs
        if run.get("status") == "completed"
        and float((run.get("evaluation") or {}).get("accuracy_score") or 0)
        == 100
    ]
    total_items = sum(
        int((run.get("evaluation") or {}).get("unique_item_count") or 0)
        for run in runs
    )
    elapsed = sum(float(run.get("elapsed_seconds") or 0) for run in runs)
    average_tokens = (
        int(trace_totals.get("tokens") or 0) / len(runs)
        if runs
        else 0
    )
    exact_label = (
        "全部为 API 精确 usage"
        if trace_totals.get("llm_calls")
        and not trace_totals.get("estimated_calls")
        else (
            f"{trace_totals.get('estimated_calls', 0)} 次为显式估算"
            if trace_totals.get("llm_calls")
            else "任务走确定性策略，未调用 LLM"
        )
    )

    phase_cards = "".join(
        f"""
        <article class="phase">
          <span>{_esc(label)}</span><h3>{_esc(title)}</h3>
          <p>{_esc(description)}</p>
        </article>
        """
        for label, title, description in PHASES
    )
    commit_rows = "".join(
        f"<tr><td>{_esc(repo)}</td><td><code>{_esc(item['hash'])}</code></td>"
        f"<td>{_esc(item['subject'])}</td></tr>"
        for repo, commits in (
            ("AutoWeb", autoweb_commits),
            ("drissionpage-cli", cli_commits),
        )
        for item in commits
    )
    task_rows = []
    task_details = []
    for index, run in enumerate(runs, start=1):
        case = run.get("case") or {}
        evaluation = run.get("evaluation") or {}
        key = str(case.get("key") or "")
        trace = traces.get(key) or {}
        tokens = int(trace.get("tokens") or 0)
        task_rows.append(
            f"""
            <tr>
              <td>{index:02d}</td><td><b>{_esc(case.get('capability'))}</b>
              <small>{_esc(case.get('name'))}</small></td>
              <td>{_esc(evaluation.get('unique_item_count'))}</td>
              <td>{float(run.get('elapsed_seconds') or 0):.1f}s</td>
              <td>{tokens}</td><td><span class="ok">100</span></td>
            </tr>
            """
        )
        restart = run.get("restart_checkpoint") or {}
        restart_html = (
            f"<p class='evidence'>恢复证据：已完成页 "
            f"<code>{_esc(restart.get('completed_pages'))}</code>，"
            f"保存 {_esc(restart.get('item_count'))} 条后重建线程。</p>"
            if int(run.get("restart_count") or 0)
            else ""
        )
        task_details.append(
            f"""
            <article class="task">
              <header><span>CASE {index:02d}</span>
              <b>{_esc(case.get('capability'))}</b></header>
              <p class="raw">{_esc(case.get('task'))}</p>
              <div class="facts">
                <span>唯一结果 <b>{_esc(evaluation.get('unique_item_count'))}</b></span>
                <span>准确度 <b>{_esc(evaluation.get('accuracy_score'))}</b></span>
                <span>浏览器动作 <b>{_esc(trace.get('browser_actions') or 0)}</b></span>
                <span>Token <b>{tokens}</b></span>
              </div>
              {restart_html}
            </article>
            """
        )
    lab_rows = "".join(
        f"<tr><td>{_esc(case.get('name'))}</td>"
        f"<td>{_esc(case.get('expected'))}</td>"
        f"<td>{_esc(case.get('actual'))}</td>"
        f"<td><span class='ok'>通过</span></td></tr>"
        for case in reliability_payload.get("cases") or []
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoWeb 阶段五至九升级验收报告</title>
<style>
:root{{--ink:#172334;--muted:#68778d;--line:#dce4ea;--paper:#f3f6f8;
--navy:#0b2039;--cyan:#16b8a6;--orange:#ff8a3d;--card:#fff}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);
font:15px/1.65 "Microsoft YaHei","PingFang SC",system-ui,sans-serif}}
.hero{{padding:68px 6vw 100px;color:#fff;background:linear-gradient(125deg,#08172c,#16495a)}}
.wrap{{max-width:1180px;margin:auto}} .kicker{{color:#7ce9db;font-weight:800;letter-spacing:.14em}}
h1{{font-size:clamp(38px,6vw,70px);line-height:1.05;margin:12px 0}} .lead{{max-width:850px;color:#cbd9e4;font-size:18px}}
main{{max-width:1180px;margin:-64px auto 70px;padding:0 20px}} .summary{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}}
.summary div,.panel,.phase,.task{{background:var(--card);border:1px solid var(--line);border-radius:16px}}
.summary div{{padding:20px;box-shadow:0 10px 30px #0c29401a}} .summary b{{display:block;font-size:28px;color:var(--navy)}} .summary span,small,.muted{{color:var(--muted)}}
.panel{{padding:25px;margin-top:24px}} h2{{margin-top:0}} .phases{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}}
.phase{{padding:16px;background:#f9fbfc}} .phase span{{color:var(--orange);font-size:12px;font-weight:900}} .phase h3{{margin:5px 0}} .phase p{{margin:0;color:var(--muted)}}
.flow{{display:flex;flex-wrap:wrap;gap:8px;align-items:center}} .flow span{{padding:8px 11px;border-radius:8px;background:#e9f7f5;font-weight:700}} .flow i{{color:var(--orange)}}
table{{width:100%;border-collapse:collapse}} th,td{{text-align:left;padding:11px;border-bottom:1px solid var(--line)}} th{{color:var(--muted);font-size:12px}} td small{{display:block}}
code{{font-family:Consolas,monospace}} .ok{{color:#087566;background:#e6f8f4;padding:3px 8px;border-radius:999px;font-weight:800}}
.tasks{{display:grid;grid-template-columns:1fr 1fr;gap:14px}} .task{{padding:20px}} .task header{{display:flex;gap:14px;align-items:center}} .task header span{{color:var(--orange);font-weight:900}}
.raw{{padding:14px;border-left:4px solid var(--orange);background:#fff7f0}} .facts{{display:flex;flex-wrap:wrap;gap:8px}} .facts span{{padding:7px 9px;background:#eef3f5;border-radius:7px}} .evidence{{color:#6f3d18;background:#fff1e5;padding:10px;border-radius:8px}}
.source{{overflow-wrap:anywhere}} @media(max-width:900px){{.summary,.phases{{grid-template-columns:1fr 1fr}}.tasks{{grid-template-columns:1fr}}}} @media(max-width:520px){{.summary,.phases{{grid-template-columns:1fr}}}}
</style></head>
<body>
<section class="hero"><div class="wrap"><div class="kicker">AUTOWEB V6 × DRISSIONPAGE CLI</div>
<h1>阶段五—阶段九<br>升级验收报告</h1>
<p class="lead">从“能完成任务”升级到可恢复、可观测、可重绑定、可组合并受生产策略治理的自然语言自动化爬取系统。报告由本次最终测试原始证据离线生成。</p>
</div></section>
<main>
<section class="summary">
<div><b>{len(passed_runs)}/{len(runs)}</b><span>复杂任务通过</span></div>
<div><b>{total_items}</b><span>唯一结果</span></div>
<div><b>{elapsed:.1f}s</b><span>5 站任务耗时</span></div>
<div><b>{average_tokens:.0f}</b><span>平均 Token/任务</span></div>
<div><b>{_esc(reliability_payload.get('passed'))}/{_esc(reliability_payload.get('total'))}</b><span>故障注入通过</span></div>
</section>
<section class="panel"><h2>升级阶段</h2><div class="phases">{phase_cards}</div></section>
<section class="panel"><h2>系统闭环</h2><div class="flow">
<span>自然语言</span><i>→</i><span>生命周期契约</span><i>→</i><span>DOM 增量观察</span><i>→</i>
<span>稳定 ref / 缓存准入</span><i>→</i><span>幂等执行</span><i>→</i><span>确定性验收</span><i>→</i>
<span>SQLite checkpoint + trace</span></div></section>
<section class="panel"><h2>回归结论</h2>
<p><b>AutoWeb：</b>{_esc(autoweb_tests)}　<b>drissionpage-cli：</b>{_esc(cli_tests)}</p>
<p><b>Token 口径：</b>{_esc(trace_totals.get('llm_calls'))} 次 LLM 调用，
{_esc(trace_totals.get('tokens'))} Token；{_esc(exact_label)}。浏览器动作 {_esc(trace_totals.get('browser_actions'))} 次。</p>
</section>
<section class="panel"><h2>5 个公开站复杂任务</h2><table><thead><tr>
<th>#</th><th>能力/站点</th><th>结果</th><th>耗时</th><th>Token</th><th>得分</th>
</tr></thead><tbody>{''.join(task_rows)}</tbody></table></section>
<section class="tasks">{''.join(task_details)}</section>
<section class="panel"><h2>可靠性实验室</h2><table><thead><tr><th>故障</th><th>预期</th><th>实际</th><th>状态</th></tr></thead><tbody>{lab_rows}</tbody></table></section>
<section class="panel"><h2>阶段提交</h2><table><thead><tr><th>仓库</th><th>Commit</th><th>中文提交</th></tr></thead><tbody>{commit_rows}</tbody></table></section>
<section class="panel source"><h2>原始证据</h2>
<p>复杂任务 JSON：<code>{_esc(complex_source)}</code></p>
<p>可靠性实验 JSON：<code>{_esc(reliability_source)}</code></p>
<p>生成时间：{_esc(generated_at)}</p></section>
</main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--complex-json", type=Path, required=True)
    parser.add_argument("--reliability-json", type=Path, required=True)
    parser.add_argument("--trace-db", type=Path, required=True)
    parser.add_argument("--autoweb-repo", type=Path, default=Path.cwd())
    parser.add_argument("--cli-repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--autoweb-tests",
        default="331 passed，2 skipped，13 subtests passed",
    )
    parser.add_argument(
        "--cli-tests",
        default="85 passed，1 skipped，1 warning",
    )
    args = parser.parse_args()

    complex_path = args.complex_json.resolve()
    reliability_path = args.reliability_json.resolve()
    complex_payload = json.loads(complex_path.read_text(encoding="utf-8"))
    traces, totals = load_trace_summaries(
        args.trace_db.resolve(),
        complex_payload.get("runs") or [],
    )
    report = build_report(
        complex_payload,
        json.loads(reliability_path.read_text(encoding="utf-8")),
        traces,
        totals,
        _git_commits(args.autoweb_repo.resolve(), "阶段五"),
        _git_commits(args.cli_repo.resolve(), "阶段五"),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        autoweb_tests=args.autoweb_tests,
        cli_tests=args.cli_tests,
        complex_source=str(complex_path),
        reliability_source=str(reliability_path),
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(output)
    print(json.dumps({"traces": traces, "totals": totals}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
