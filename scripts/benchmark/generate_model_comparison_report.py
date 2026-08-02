"""Generate one self-contained HTML crawler and model-comparison report."""

from __future__ import annotations

import argparse
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _pass(run: dict[str, Any]) -> bool:
    checks = (run.get("evaluation") or {}).get("checks") or {}
    return bool(checks) and all(bool(value) for value in checks.values())


def _fmt_int(value: Any) -> str:
    return f"{int(value or 0):,}"


def _status(value: bool) -> str:
    return '<span class="pass">PASS</span>' if value else '<span class="fail">FAIL</span>'


def _outcome(evaluation: dict[str, Any]) -> str:
    blocker = str(evaluation.get("blocker") or "").strip()
    if blocker in {"login_required", "captcha", "app_required", "robots_denied"}:
        return f"合规阻断：{_e(blocker)}"
    if all((evaluation.get("checks") or {}).values()):
        return f"安全终点（伴随 {_e(blocker)}）" if blocker else "自主完成"
    return "未完成"


def _model_rows(summary: list[dict[str, Any]]) -> str:
    rows = []
    for item in summary:
        rows.append(
            "<tr>"
            f"<td><strong>{_e(item.get('model'))}</strong></td>"
            f"<td>{_e(item.get('passed'))}/{_e(item.get('run_count'))}</td>"
            f"<td>{_e(item.get('success_rate'))}%</td>"
            f"<td>{_e(item.get('average_accuracy'))}%</td>"
            f"<td>{_e(item.get('average_elapsed_seconds'))} s</td>"
            f"<td>{_fmt_int(item.get('average_tokens'))}</td>"
            f"<td>{_fmt_int(item.get('input_tokens'))} / {_fmt_int(item.get('output_tokens'))}</td>"
            f"<td>{_e(item.get('llm_call_count'))}</td>"
            f"<td>{_e(item.get('skill_selection_decisions'))}</td>"
            f"<td>{_e(', '.join(item.get('selected_skills') or []))}</td>"
            f"<td>{_e(item.get('llm_duration_seconds'))} s</td>"
            f"<td>{_e(item.get('average_browser_actions'))}</td>"
            f"<td>{_e(item.get('average_resume_count'))}</td>"
            f"<td>¥{float(item.get('total_cost_cny') or 0):.6f}</td>"
            f"<td>{_e(item.get('estimated_call_count'))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _task_rows(runs: list[dict[str, Any]]) -> str:
    rows = []
    for run in runs:
        evaluation = run.get("evaluation") or {}
        usage = run.get("usage") or {}
        case = run.get("case") or {}
        skill_selection = run.get("skill_selection") or {}
        rows.append(
            "<tr>"
            f"<td>{_e(run.get('model'))}</td>"
            f"<td><strong>{_e(case.get('key'))}</strong><br><small>{_e(case.get('capability'))}</small></td>"
            f"<td>{_status(_pass(run))}</td>"
            f"<td>{_outcome(evaluation)}</td>"
            f"<td>{_e(run.get('status'))}</td>"
            f"<td>{_e(evaluation.get('accuracy_score'))}%</td>"
            f"<td>{_e(evaluation.get('unique_item_count', evaluation.get('successful_action_count')))}</td>"
            f"<td>{_e(run.get('elapsed_seconds'))} s</td>"
            f"<td>{_fmt_int(usage.get('total_tokens'))}</td>"
            f"<td>{_fmt_int(usage.get('input_tokens'))} / {_fmt_int(usage.get('output_tokens'))}</td>"
            f"<td>{_e(usage.get('llm_call_count'))}</td>"
            f"<td>{_e(skill_selection.get('decision_count'))}</td>"
            f"<td>{_e(', '.join(skill_selection.get('selected_skills') or []))}</td>"
            f"<td>{_e(usage.get('browser_action_count'))}</td>"
            f"<td>{_e(run.get('resume_count'))}</td>"
            f"<td>¥{float(run.get('estimated_cost_cny') or 0):.6f}</td>"
            f"<td>{_e(usage.get('token_precision'))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _task_details(runs: list[dict[str, Any]]) -> str:
    cards = []
    for run in runs:
        case = run.get("case") or {}
        evaluation = run.get("evaluation") or {}
        checks = evaluation.get("checks") or {}
        check_html = "".join(
            f"<li>{_status(bool(value))} {_e(key)}</li>" for key, value in checks.items()
        )
        exception = run.get("exception")
        evidence_payload = evaluation.get("item_sample")
        if evidence_payload is None:
            evidence_payload = {
                key: evaluation.get(key)
                for key in (
                    "domains",
                    "final_url",
                    "successful_action_count",
                    "generated_action_count",
                    "safe_stop",
                    "blocker",
                    "unsafe_click",
                )
            }
        sample = json.dumps(evidence_payload, ensure_ascii=False, indent=2)
        cards.append(
            f"""
            <article class="case-card">
              <header><div><span>{_e(run.get('model'))}</span><h3>{_e(case.get('name'))}</h3></div>{_status(_pass(run))}</header>
              <p class="task">{_e(case.get('task'))}</p>
              <p><strong>结果：</strong>{_e(evaluation.get('unique_item_count'))} 条唯一记录，准确检查得分 {_e(evaluation.get('accuracy_score'))}%，耗时 {_e(run.get('elapsed_seconds'))} 秒。</p>
              <ul class="checks">{check_html}</ul>
              {f'<p class="error"><strong>异常：</strong>{_e(exception)}</p>' if exception else ''}
              <details><summary>样本数据与证据</summary><pre>{_e(sample)}</pre><p>来源：{_e(run.get('source_artifact'))}</p></details>
            </article>
            """
        )
    return "".join(cards)


def _optimization_rows(items: list[dict[str, Any]]) -> str:
    rows = []
    for item in items:
        baseline = item.get("baseline") or {}
        final = item.get("final") or {}
        rows.append(
            "<tr>"
            f"<td><strong>{_e(item.get('case'))}</strong></td>"
            f"<td>{_status(bool(baseline.get('passed')))} {_e(baseline.get('status'))}</td>"
            f"<td>{_e(baseline.get('elapsed_seconds'))} s</td>"
            f"<td>{_fmt_int(baseline.get('total_tokens'))}</td>"
            f"<td>{_status(bool(final.get('passed')))} {_e(final.get('outcome') or final.get('status'))}</td>"
            f"<td>{_e(final.get('elapsed_seconds'))} s</td>"
            f"<td>{_fmt_int(final.get('total_tokens'))}</td>"
            f"<td>{_e(item.get('token_reduction_percent'))}%</td>"
            "</tr>"
        )
    return "".join(rows)


def _winner(summary: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not summary:
        return None
    return sorted(
        summary,
        key=lambda row: (
            -float(row.get("success_rate") or 0),
            -float(row.get("average_accuracy") or 0),
            float(row.get("average_elapsed_seconds") or 0),
            float(row.get("average_tokens") or 0),
        ),
    )[0]


def generate_report(matrix_path: Path, output_path: Path) -> Path:
    payload = json.loads(matrix_path.read_text(encoding="utf-8"))
    summary = payload.get("summary") or []
    runs = payload.get("runs") or []
    configuration = payload.get("configuration") or {}
    optimization = payload.get("optimization_evidence") or {}
    optimization_items = optimization.get("items") or []
    winner = _winner(summary)
    total_passed = sum(_pass(run) for run in runs)
    exact_usage = sum(
        int((run.get("usage") or {}).get("estimated_call_count") or 0) == 0
        and str((run.get("usage") or {}).get("token_precision") or "")
        in {"provider_reported", "no_llm_calls"}
        for run in runs
    )
    total_llm_calls = sum(
        int((run.get("usage") or {}).get("llm_call_count") or 0) for run in runs
    )
    skill_selection_mode = str(configuration.get("skill_selection_mode") or "")
    is_progressive_skill_matrix = (
        skill_selection_mode == "llm_metadata_then_progressive_body_load"
    )
    if not is_progressive_skill_matrix:
        verdict = (
            "<strong>历史矩阵，不可用于比较模型推理能力。</strong>该数据生成于旧的站点路由架构："
            "任务路径可在模型选择技能之前由 Python 规则直接短路。成功率、耗时和 0 Token 只能表示"
            "旧站点适配器的执行结果。请使用升级后的 name + description 元数据选择架构重新运行三模型矩阵。"
        )
    elif summary and total_llm_calls == 0:
        fastest = min(summary, key=lambda item: float(item.get("average_elapsed_seconds") or 1e30))
        verdict = (
            "矩阵声明使用 LLM 技能选择，但记录的 LLM 调用仍为 0，证据不完整，不能比较模型能力。"
            f"观察到的浏览器平均耗时最低为 <strong>{_e(fastest.get('model'))}</strong> "
            f"({_e(fastest.get('average_elapsed_seconds'))} 秒)，该差异主要反映页面与网络波动。"
        )
    else:
        verdict = (
            f"综合排序第一为 <strong>{_e(winner.get('model'))}</strong>："
            f"成功率 {_e(winner.get('success_rate'))}%，平均准确得分 {_e(winner.get('average_accuracy'))}%，"
            f"平均耗时 {_e(winner.get('average_elapsed_seconds'))} 秒，平均 {_fmt_int(winner.get('average_tokens'))} Token。"
            if winner
            else "没有可用于排序的有效运行。"
        )
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoWeb 实战爬虫与模型对比报告</title>
<style>
:root{{--ink:#172033;--muted:#687087;--paper:#f4f1ea;--card:#fff;--line:#d9d5cc;--teal:#0a746e;--orange:#e7682b;--red:#b83434;--green:#18794e}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif}}
main{{max-width:1500px;margin:auto;padding:42px 28px 80px}}h1{{font-size:clamp(34px,5vw,72px);line-height:1.02;margin:10px 0 18px;letter-spacing:-.04em}}h2{{margin-top:46px;font-size:29px}}.kicker{{color:var(--orange);font-weight:900;letter-spacing:.12em}}.lead{{max-width:960px;font-size:19px;color:var(--muted)}}
.summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:28px 0}}.metric,.panel,.case-card{{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:0 8px 30px #1820330a}}.metric{{padding:18px}}.metric b{{display:block;font-size:30px;color:var(--teal)}}.metric span,small{{color:var(--muted)}}.panel{{padding:22px;overflow:auto}}
table{{border-collapse:collapse;width:100%;min-width:1450px}}.task-matrix{{min-width:1900px}}th,td{{padding:11px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);white-space:nowrap}}.pass{{color:var(--green);font-weight:900}}.fail{{color:var(--red);font-weight:900}}
.verdict{{border-left:5px solid var(--orange);padding:18px 22px;background:#fff8f1;border-radius:0 14px 14px 0;font-size:18px}}.cases{{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}}.case-card{{padding:20px}}.case-card header{{display:flex;justify-content:space-between;gap:15px;align-items:start}}.case-card h3{{margin:2px 0}}.case-card header span{{color:var(--teal);font-weight:800}}.task{{color:var(--muted)}}.checks{{columns:2;padding-left:20px}}details{{margin-top:12px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#151b28;color:#e8edf7;padding:14px;border-radius:10px;max-height:360px;overflow:auto}}.error{{color:var(--red)}}code{{background:#ebe7df;padding:2px 5px;border-radius:5px}}
@media(max-width:900px){{.summary{{grid-template-columns:repeat(2,1fr)}}.cases{{grid-template-columns:1fr}}}}@media(max-width:520px){{main{{padding:26px 15px}}.summary{{grid-template-columns:1fr}}}}
</style></head><body><main>
<div class="kicker">AUTOWEB · REAL BROWSER MODEL MATRIX</div><h1>实战爬虫与模型对比报告</h1>
<p class="lead">同一 AutoWeb 代码、同一百炼兼容端点、无头本地 Chromium、关闭跨模型缓存与确定性任务契约规划，并统一关闭混合思考模式。报告同时呈现任务正确性、端到端耗时、LLM 调用耗时和 API 返回的 Token 用量。</p>
<section class="summary"><div class="metric"><b>{len(runs)}</b><span>实际任务运行</span></div><div class="metric"><b>{total_passed}</b><span>完整通过</span></div><div class="metric"><b>{len(summary)}</b><span>对比模型</span></div><div class="metric"><b>{exact_usage}/{len(runs)}</b><span>精确 Token 证据</span></div></section>
<p class="verdict">{verdict}</p>
<h2>升级前后证据</h2><section class="panel"><p>同一 DeepSeek 配置的代表性旧版失败与最终成功运行对比；时间增加可能表示最终版本执行了更多真实步骤，Token 降低才是本节的主要效率指标。</p><table><thead><tr><th>任务</th><th>旧版结果</th><th>旧版耗时</th><th>旧版 Token</th><th>最终结果</th><th>最终耗时</th><th>最终 Token</th><th>Token 降幅</th></tr></thead><tbody>{_optimization_rows(optimization_items)}</tbody></table><p><strong>代表性旧版合计：</strong>{_fmt_int(optimization.get('baseline_total_tokens'))} Token；<strong>最终合计：</strong>{_fmt_int(optimization.get('final_total_tokens'))} Token；节省 {_fmt_int(optimization.get('tokens_saved'))} Token。</p></section>
<h2>模型总表</h2><section class="panel"><table><thead><tr><th>模型</th><th>通过</th><th>成功率</th><th>平均准确</th><th>平均总耗时</th><th>平均 Token</th><th>输入 / 输出 Token</th><th>LLM 调用</th><th>技能选择</th><th>已选技能</th><th>LLM 累计耗时</th><th>平均浏览器动作</th><th>平均恢复轮数</th><th>Token 成本（CNY）</th><th>估算调用</th></tr></thead><tbody>{_model_rows(summary)}</tbody></table></section>
<h2>爬虫任务矩阵</h2><section class="panel"><table class="task-matrix"><thead><tr><th>模型</th><th>任务</th><th>验收</th><th>结果类型</th><th>Agent 状态</th><th>准确得分</th><th>唯一记录</th><th>总耗时</th><th>Token</th><th>输入 / 输出</th><th>LLM 调用</th><th>技能选择</th><th>已选技能</th><th>浏览器动作</th><th>恢复轮数</th><th>成本（CNY）</th><th>Token 证据</th></tr></thead><tbody>{_task_rows(runs)}</tbody></table></section>
<h2>逐任务证据</h2><section class="cases">{_task_details(runs)}</section>
<h2>实验口径</h2><section class="panel"><p>模型：<code>{_e(', '.join(configuration.get('models') or []))}</code></p><p>任务：<code>{_e(', '.join(configuration.get('cases') or []))}</code></p><p>每模型每任务重复 {_e(configuration.get('repeats'))} 次；最大恢复轮数 {_e(configuration.get('max_resumes'))}；思考模式 {_e(configuration.get('enable_thinking'))}；确定性任务契约规划 {_e(configuration.get('task_contract_planner'))}；技能选择模式 <code>{_e(skill_selection_mode or 'legacy_pre_llm_routing')}</code>；{_e(configuration.get('cache_isolation'))}。</p><p>新架构仅把技能的 <code>name + description</code> 目录交给模型；模型选择后才加载对应 <code>SKILL.md</code> 正文。站点技能不再产生预设动作，也不能绕过安全策略。旧矩阵若没有声明该模式，只能作为历史站点适配器证据，必须重跑后才能比较模型。</p><p>“完整通过”要求所有确定性检查为真。遇到 robots、验证码、登录或 App 门槛时，报告单列为“合规阻断”，不冒充下游交易完成。</p><p>成本按百炼华北2（北京）公开原价和供应商 usage 估算，单位人民币；未计活动优惠与缓存折扣。价格来源：{''.join(f'<a href="{_e(url)}">{_e(model)}</a> ' for model, url in (configuration.get('pricing_sources') or {}).items())}</p><p>矩阵证据：<code>{_e(matrix_path)}</code></p><p>生成时间：{_e(datetime.now().isoformat())}</p></section>
</main></body></html>"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path)
    parser.add_argument("--output", type=Path, default=Path("output/reports/autoweb_model_matrix.html"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(generate_report(args.matrix.resolve(), args.output.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
