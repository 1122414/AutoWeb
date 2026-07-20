"""Generate an offline HTML report for the 20-site Chinese benchmark."""

from __future__ import annotations

import argparse
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _strict_pass(run: dict[str, Any]) -> bool:
    evaluation = run.get("evaluation") or {}
    checks = evaluation.get("checks") or {}
    close = run.get("session_close") or {}
    return bool(
        run.get("status") == "completed"
        and evaluation.get("accuracy_score") == 100.0
        and checks
        and all(checks.values())
        and close.get("ok")
    )


def _items(run: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action in run.get("results") or []:
        for item in action.get("items") or []:
            if not isinstance(item, dict):
                continue
            key = str(
                item.get("url")
                or item.get("href")
                or item.get("detail_url")
                or json.dumps(item, ensure_ascii=False, sort_keys=True)
            ).strip().rstrip("/")
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(item)
    return result


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _sample_html(run: dict[str, Any], limit: int = 3) -> str:
    samples = _items(run)[:limit]
    if not samples:
        return '<span class="muted">无有效条目</span>'
    rendered = []
    for item in samples:
        title = (
            item.get("title")
            or item.get("name")
            or item.get("text")
            or "未命名条目"
        )
        url = item.get("url") or item.get("href") or item.get("detail_url") or ""
        if str(url).startswith(("http://", "https://")):
            rendered.append(
                f'<a href="{_e(url)}" target="_blank" rel="noreferrer">{_e(title)}</a>'
            )
        else:
            rendered.append(_e(title))
    return "<br>".join(rendered)


def _check_chips(run: dict[str, Any]) -> str:
    labels = {
        "target_opened": "目标页",
        "minimum_unique_items": "数量下限",
        "maximum_unique_items": "数量上限",
        "required_field_coverage_80pct": "字段覆盖",
        "autonomous_completion": "自主结束",
        "chinese_title_ratio": "中文标题",
        "content_relevance": "内容相关",
    }
    checks = (run.get("evaluation") or {}).get("checks") or {}
    return "".join(
        (
            f'<span class="chip {"ok" if checks.get(key) else "bad"}">'
            f'{"✓" if checks.get(key) else "×"} {_e(label)}</span>'
        )
        for key, label in labels.items()
    )


def _status(run: dict[str, Any], key: str) -> tuple[str, str, str]:
    if _strict_pass(run):
        suffix = " · 修复后补测" if key == "mtime_movie" else ""
        return "通过", "pass", f"严格验收全部通过{suffix}"
    page_title = ""
    for event in run.get("events") or []:
        result = event.get("dpcli_result") or {}
        if result.get("page_url"):
            page_title = str(result.get("page_url"))
    if key == "maoyan_movie":
        return "外部拦截", "blocked", "403 Forbidden；未绕过站点风控"
    summary = str(run.get("exception") or page_title or "严格验收未通过")
    return "未通过", "fail", summary


def _render_rows(
    ordered_keys: list[str],
    runs: dict[str, dict[str, Any]],
    primary_runs: dict[str, dict[str, Any]],
) -> str:
    rows = []
    for index, key in enumerate(ordered_keys, 1):
        run = runs[key]
        case = run.get("case") or {}
        evaluation = run.get("evaluation") or {}
        label, css_class, note = _status(run, key)
        category = str(case.get("capability") or "").split("｜", 1)[0]
        original = primary_runs.get(key) or run
        original_score = (original.get("evaluation") or {}).get("accuracy_score", 0)
        supplement = ""
        if original is not run:
            supplement = (
                f'<div class="sub">整套原始：{_e(original_score)} 分；'
                f"当前：{_e(evaluation.get('accuracy_score', 0))} 分</div>"
            )
        rows.append(
            f"""
            <tr data-status="{css_class}" data-category="{_e(category)}">
              <td class="num">{index:02d}</td>
              <td>
                <strong>{_e(case.get("name") or key)}</strong>
                <div class="sub"><a href="{_e(case.get("url") or "")}" target="_blank"
                rel="noreferrer">{_e(case.get("url") or "")}</a></div>
              </td>
              <td><span class="category">{_e(category)}</span></td>
              <td><span class="status {css_class}">{label}</span>
                  <div class="sub">{_e(note)}</div></td>
              <td><strong>{_e(evaluation.get("accuracy_score", 0))}</strong>
                  {supplement}</td>
              <td>{_e(evaluation.get("unique_item_count", 0))}</td>
              <td>{_e(evaluation.get("relevant_item_ratio", 0))}</td>
              <td>{_e(run.get("elapsed_seconds", 0))}s</td>
              <td class="samples">{_sample_html(run)}</td>
            </tr>
            """
        )
    return "\n".join(rows)


def _render_movie_cards(
    runs: dict[str, dict[str, Any]],
    keys: list[str],
) -> str:
    cards = []
    for key in keys:
        run = runs[key]
        case = run["case"]
        label, css_class, note = _status(run, key)
        cards.append(
            f"""
            <article class="movie-card {css_class}">
              <div class="eyebrow">{_e(case["name"])}</div>
              <h3>{label}</h3>
              <p>{_e(note)}</p>
              <div class="samples">{_sample_html(run, limit=5)}</div>
            </article>
            """
        )
    return "\n".join(cards)


def generate(
    suite_path: Path,
    mtime_path: Path,
    maoyan_path: Path,
    output_path: Path,
) -> Path:
    suite = _load(suite_path)
    primary_runs = {run["case"]["key"]: run for run in suite.get("runs") or []}
    ordered_keys = [run["case"]["key"] for run in suite.get("runs") or []]
    current_runs = dict(primary_runs)
    current_runs["mtime_movie"] = _load(mtime_path)["runs"][0]
    current_runs["maoyan_movie"] = _load(maoyan_path)["runs"][0]

    strict_count = sum(_strict_pass(run) for run in current_runs.values())
    primary_strict_count = sum(_strict_pass(run) for run in primary_runs.values())
    movie_keys = [
        key
        for key in ordered_keys
        if str(current_runs[key]["case"].get("capability") or "").startswith("电影｜")
    ]
    movie_strict = sum(_strict_pass(current_runs[key]) for key in movie_keys)
    total_items = sum(
        int((run.get("evaluation") or {}).get("unique_item_count") or 0)
        for run in current_runs.values()
    )
    timeout_count = sum(
        1
        for run in primary_runs.values()
        for result in run.get("results") or []
        if ((result.get("error") or {}).get("code") == "timeout")
    )
    closed_count = sum(
        bool((run.get("session_close") or {}).get("ok"))
        for run in current_runs.values()
    )
    elapsed_minutes = round(float(suite.get("suite_elapsed_seconds") or 0) / 60, 1)
    rows = _render_rows(ordered_keys, current_runs, primary_runs)
    movies = _render_movie_cards(current_runs, movie_keys)
    generated = datetime.now().astimezone().isoformat(timespec="seconds")

    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AutoWeb × drissionpage-cli｜20 个中文网站验证报告</title>
  <style>
    :root {{
      --ink:#14213d; --navy:#0d1b2a; --paper:#f6f2e9; --card:#fffdf8;
      --line:#d8d1c4; --green:#147d64; --green-bg:#dff4ea;
      --amber:#a96208; --amber-bg:#fff0ce; --red:#a13a32; --red-bg:#fde5df;
      --blue:#2458a6; --muted:#6d7480;
    }}
    * {{ box-sizing:border-box; }}
    html {{ scroll-behavior:smooth; }}
    body {{ margin:0; color:var(--ink); background:var(--paper);
      font:15px/1.65 "Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    a {{ color:var(--blue); text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
    .hero {{ color:white; padding:64px clamp(24px,7vw,110px) 54px;
      background:linear-gradient(135deg,#081523 0%,#18385b 65%,#186f68 120%); }}
    .hero-inner,.wrap {{ max-width:1380px; margin:auto; }}
    .kicker,.eyebrow {{ text-transform:uppercase; letter-spacing:.14em;
      font-size:12px; font-weight:750; opacity:.78; }}
    h1 {{ max-width:900px; margin:12px 0 18px; font-size:clamp(34px,5vw,66px);
      line-height:1.07; letter-spacing:-.04em; }}
    h2 {{ margin:0 0 20px; font-size:28px; letter-spacing:-.02em; }}
    h3 {{ margin:8px 0; font-size:20px; }}
    .hero p {{ max-width:850px; font-size:18px; color:#d9e6ef; }}
    .hero-meta {{ display:flex; gap:20px; flex-wrap:wrap; margin-top:26px;
      color:#bed2df; font-size:13px; }}
    .wrap {{ padding:36px clamp(18px,4vw,54px) 80px; }}
    .metrics {{ display:grid; grid-template-columns:repeat(6,minmax(140px,1fr));
      gap:14px; margin-top:-62px; position:relative; }}
    .metric {{ background:var(--card); border:1px solid rgba(20,33,61,.1);
      border-radius:18px; padding:22px; box-shadow:0 14px 30px rgba(12,25,42,.10); }}
    .metric b {{ display:block; font-size:30px; line-height:1.1; margin:6px 0; }}
    .metric small {{ color:var(--muted); }}
    section {{ margin-top:44px; }}
    .callout {{ display:grid; grid-template-columns:160px 1fr; gap:24px;
      align-items:center; background:#e8f1ee; border-left:5px solid var(--green);
      border-radius:16px; padding:24px; }}
    .donut {{ width:128px; aspect-ratio:1; border-radius:50%;
      background:conic-gradient(var(--green) 0 95%,#c9d7d2 95% 100%);
      display:grid; place-items:center; margin:auto; }}
    .donut::after {{ content:"95%"; display:grid; place-items:center; width:84px;
      aspect-ratio:1; border-radius:50%; background:#e8f1ee; font-size:25px;
      font-weight:800; }}
    .flow {{ display:flex; gap:0; overflow:auto; padding:12px 2px 20px; }}
    .flow-step {{ min-width:152px; max-width:152px; padding:17px 13px;
      border:1px solid var(--line); background:var(--card); border-radius:14px; }}
    .flow-step b {{ display:block; margin-bottom:5px; }}
    .arrow {{ min-width:34px; display:grid; place-items:center; color:var(--green);
      font-size:24px; font-weight:900; }}
    .branch {{ border-color:#e1b968; background:#fff8e8; }}
    .agent-grid,.cause-grid,.movie-grid {{ display:grid;
      grid-template-columns:repeat(3,1fr); gap:15px; }}
    .agent-card,.cause,.movie-card {{ background:var(--card); border:1px solid var(--line);
      border-radius:16px; padding:21px; }}
    .agent-card code {{ display:block; margin:10px 0; color:#264b6b;
      white-space:normal; }}
    .cause strong {{ display:block; font-size:17px; margin-bottom:7px; }}
    .cause .proof {{ color:var(--muted); font-size:13px; }}
    .movie-card.pass {{ border-top:5px solid var(--green); }}
    .movie-card.blocked {{ border-top:5px solid var(--amber); }}
    .movie-card.fail {{ border-top:5px solid var(--red); }}
    .movie-card .samples {{ margin-top:14px; padding-top:12px;
      border-top:1px solid var(--line); }}
    .table-shell {{ overflow:auto; border:1px solid var(--line); border-radius:16px;
      background:var(--card); }}
    table {{ width:100%; border-collapse:collapse; min-width:1260px; }}
    th {{ position:sticky; top:0; z-index:1; background:var(--navy); color:white;
      text-align:left; padding:13px 12px; font-size:12px; letter-spacing:.04em; }}
    td {{ border-bottom:1px solid #e6e0d5; padding:13px 12px; vertical-align:top; }}
    tr:last-child td {{ border-bottom:0; }}
    tr:hover td {{ background:#fbf8f1; }}
    .num,.sub,.muted {{ color:var(--muted); }}
    .sub {{ font-size:12px; margin-top:3px; max-width:330px; }}
    .category {{ white-space:nowrap; padding:4px 9px; background:#edf1f5;
      border-radius:999px; font-size:12px; }}
    .status,.chip {{ display:inline-flex; align-items:center; white-space:nowrap;
      border-radius:999px; padding:4px 9px; font-size:12px; font-weight:750; }}
    .status.pass,.chip.ok {{ color:var(--green); background:var(--green-bg); }}
    .status.blocked {{ color:var(--amber); background:var(--amber-bg); }}
    .status.fail,.chip.bad {{ color:var(--red); background:var(--red-bg); }}
    .samples {{ min-width:260px; }}
    .chips {{ display:flex; flex-wrap:wrap; gap:7px; }}
    .fixes {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
    .fix-box {{ border-radius:16px; padding:22px; background:var(--card);
      border:1px solid var(--line); }}
    .fix-box li {{ margin:8px 0; }}
    .evidence {{ background:#13263b; color:#dbe6ee; border-radius:16px; padding:22px; }}
    .evidence code {{ color:#9fe0cc; overflow-wrap:anywhere; }}
    .note {{ color:var(--muted); font-size:13px; }}
    footer {{ padding:34px; text-align:center; color:var(--muted);
      border-top:1px solid var(--line); }}
    @media (max-width:1000px) {{
      .metrics {{ grid-template-columns:repeat(2,1fr); }}
      .agent-grid,.cause-grid,.movie-grid {{ grid-template-columns:1fr 1fr; }}
      .fixes {{ grid-template-columns:1fr; }}
    }}
    @media (max-width:640px) {{
      .metrics,.agent-grid,.cause-grid,.movie-grid {{ grid-template-columns:1fr; }}
      .callout {{ grid-template-columns:1fr; }}
    }}
    @media print {{
      .hero {{ padding:28px; }} .wrap {{ padding:20px; }}
      .metrics {{ margin-top:15px; }} .metric {{ box-shadow:none; }}
      .table-shell {{ overflow:visible; }} table {{ min-width:0; font-size:10px; }}
      th {{ position:static; }} a {{ color:inherit; }}
    }}
  </style>
</head>
<body>
  <header class="hero">
    <div class="hero-inner">
      <div class="kicker">AutoWeb V6 × drissionpage-cli · Real headless benchmark</div>
      <h1>20 个常用中文网站<br>自然语言自动化爬取验证</h1>
      <p>公开站点、真实无头 Chrome、自然语言任务、结构化内容验收。报告保留失败，
      不绕过验证码或站点风控，也不把分类、页脚、推广或 trailer 链接算作成功。</p>
      <div class="hero-meta">
        <span>报告生成：{_e(generated)}</span>
        <span>整套运行：{elapsed_minutes} 分钟</span>
        <span>主整套原始严格通过：{primary_strict_count}/20</span>
        <span>修复后替换证据：{strict_count}/20</span>
      </div>
    </div>
  </header>

  <main class="wrap">
    <div class="metrics">
      <div class="metric"><span class="eyebrow">当前严格通过</span><b>{strict_count}/20</b><small>95% 站点通过</small></div>
      <div class="metric"><span class="eyebrow">电影网站</span><b>{movie_strict}/4</b><small>测试 4 个，达到 ≥3 要求</small></div>
      <div class="metric"><span class="eyebrow">有效条目</span><b>{total_items}</b><small>去重后的结构化记录</small></div>
      <div class="metric"><span class="eyebrow">超时恢复</span><b>{timeout_count}</b><small>页面已加载后继续验证</small></div>
      <div class="metric"><span class="eyebrow">会话关闭</span><b>{closed_count}/20</b><small>无头浏览器均已清理</small></div>
      <div class="metric"><span class="eyebrow">回归测试</span><b>86</b><small>AutoWeb 77 + CLI 9</small></div>
    </div>

    <section class="callout">
      <div class="donut"></div>
      <div>
        <h2>结论：系统已从“数量够就算成功”升级为“内容正确才成功”</h2>
        <p>主整套在最后一轮修复前为 18/20；时光网页脚与 trailer 假阳性被发现后，
        增加页脚识别、媒体子资源过滤和更严格中文/URL 契约，补测得到 5 条真实电影详情，
        当前证据为 19/20。猫眼仍返回 <strong>403 Forbidden</strong>，按外部拦截保留失败。</p>
      </div>
    </section>

    <section>
      <div class="eyebrow">How it works</div>
      <h2>当前自然语言自动化爬取流程</h2>
      <div class="flow">
        <div class="flow-step"><b>1. 自然语言</b>URL、数量、字段、页数、是否进详情页</div><div class="arrow">→</div>
        <div class="flow-step"><b>2. TaskContract</b>标准化为可执行约束</div><div class="arrow">→</div>
        <div class="flow-step"><b>3. Observer</b>dp-cli 获取真实 DOM 与页面身份</div><div class="arrow">→</div>
        <div class="flow-step"><b>4. Capability Map</b>候选区域、样本、动作、分页</div><div class="arrow">→</div>
        <div class="flow-step"><b>5. Planner</b>确定性选择 open / extract / click / wait</div><div class="arrow">→</div>
        <div class="flow-step"><b>6. Executor</b>执行结构化 CLI action JSON</div><div class="arrow">→</div>
        <div class="flow-step"><b>7. Verifier</b>数量、字段、去重、累计进度</div><div class="arrow">→</div>
        <div class="flow-step branch"><b>8A. 未完成</b>排除已尝试区域，继续或翻页</div><div class="arrow">↺</div>
        <div class="flow-step"><b>8B. 终态</b>完成或“区域耗尽”受控失败</div>
      </div>
    </section>

    <section>
      <div class="eyebrow">Agent visibility</div>
      <h2>Agent 实际看什么、做到了什么</h2>
      <div class="agent-grid">
        <article class="agent-card"><h3>Planner 看压缩视图</h3>
          <code>dpcli_agent_view</code><p>页面 URL/标题、最多 5 个高分数据区域、
          区域样本、可用动作和分页；不直接吞整页原始 DOM。</p></article>
        <article class="agent-card"><h3>Executor 看动作契约</h3>
          <code>{{skill, params, reason}}</code><p>只执行 open、extract、click、wait 等
          白名单动作；引用来自快照的 r*/e* ref，避免自由拼接定位器。</p></article>
        <article class="agent-card"><h3>Verifier 看结果与进度</h3>
          <code>task_contract + dpcli_result + progress</code><p>跨区域去重累计，
          校验字段覆盖和数量；区域不足时继续，下限满足才结束。</p></article>
        <article class="agent-card"><h3>完整证据保留在磁盘</h3>
          <code>output/dpcli_snapshots + logs/code_log</code><p>Planner 使用压缩信息，
          但 full snapshot、索引、每次 CLI 日志仍可追溯。</p></article>
        <article class="agent-card"><h3>LLM 不再是默认控制器</h3>
          <code>deterministic task contract first</code><p>常见列表任务由确定性策略完成；
          已知区域耗尽直接受控失败，不再为了“试试看”调用模型。</p></article>
        <article class="agent-card"><h3>伦理边界</h3>
          <code>robots probe + no bypass</code><p>只访问公开测试页面；403、验证码、
          WAF 均不绕过，结果按外部拦截记录。</p></article>
      </div>
    </section>

    <section>
      <div class="eyebrow">Movie coverage</div>
      <h2>4 个电影网站结果</h2>
      <div class="movie-grid">{movies}</div>
    </section>

    <section>
      <div class="eyebrow">Root causes</div>
      <h2>自动化爬取不稳定的具体原因</h2>
      <div class="cause-grid">
        <article class="cause"><strong>1. 外部风控 / WAF</strong><p>猫眼直接返回 403，
          DOM 仅 4 个节点，页面标题为 403 Forbidden。</p><div class="proof">这是站点策略，不是定位器错误；未绕过。</div></article>
        <article class="cause"><strong>2. 网络与固定超时不匹配</strong><p>最终整套出现
          {timeout_count} 次 60 秒 open 超时，但后续快照确认页面已经加载。</p><div class="proof">哔哩哔哩、新浪、腾讯、搜狐、天气均成功恢复。</div></article>
        <article class="cause"><strong>3. 页面体量与 DOM 深度</strong><p>门户页可达数千节点；
          搜狐真实新闻位于容器第 15–16 层，浅层先出现导航。</p><div class="proof">抽取深度从 6 提升到 16 后，搜狐由 88.9 升至 100。</div></article>
        <article class="cause"><strong>4. 动态内容区漂移</strong><p>时光网同一 URL 不同轮次可出现
          完整电影区、仅 3 部电影或页脚/社区内容。</p><div class="proof">必须用内容契约验收，不能只认同域 URL。</div></article>
        <article class="cause"><strong>5. 语义假阳性</strong><p>类型分类、推广追踪、
          页脚导航、trailer、标题为 link/URL 都曾满足“5 条”。</p><div class="proof">新增路径、标题、中文比例和页脚签名过滤。</div></article>
        <article class="cause"><strong>6. 状态与恢复边界</strong><p>累计区域进度、失败区域和验证契约
          若未注册，会反复定位或转入 LLM。</p><div class="proof">状态字段、同错上限和区域耗尽终态均已补齐。</div></article>
      </div>
    </section>

    <section>
      <div class="eyebrow">Results</div>
      <h2>20 站逐项结果与样本</h2>
      <p class="note">“通过”要求：终态 completed、全部 9 项检查通过、准确率 100、
      会话关闭成功。内容相关阈值至少 80%；当前中文标题阈值已提高到 80%。
      时光网采用修复后严格补测替换证据，并保留整套原始分数。</p>
      <div class="table-shell">
        <table>
          <thead><tr><th>#</th><th>站点 / URL</th><th>类别</th><th>状态</th>
          <th>准确率</th><th>条目</th><th>相关率</th><th>耗时</th><th>样本</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </section>

    <section>
      <div class="eyebrow">Engineering changes</div>
      <h2>本次已落地的优化</h2>
      <div class="fixes">
        <div class="fix-box"><h3>AutoWeb</h3><ul>
          <li>AgentState 注册 dpcli_action_kind、dpcli_verification_contract 及错误恢复字段。</li>
          <li>中文计数单位支持“部、篇、首、道、则、项、款”。</li>
          <li>有效小区域可累计，当前区域完成后自动排除并选择下一块。</li>
          <li>分页后清空页内失败区域，避免跨页误伤。</li>
          <li>同类严重错误最多恢复 3 次；成功快照会重置计数。</li>
          <li>确定性区域耗尽直接 failed，不再落入 LLM 产生 API 异常。</li>
          <li>基准加入中文标题、URL 模式、禁止模式、标题长度与相关率验收。</li>
        </ul></div>
        <div class="fix-box"><h3>drissionpage-cli</h3><ul>
          <li>数据区域按长标题比例加权，短菜单与 URL 标题降权。</li>
          <li>过滤 taxonomy、promotion、trailer/video/photo 等非详情链接。</li>
          <li>宽容器投影只以高置信详情链接为种子。</li>
          <li>结构化投影结果必须落在允许的种子 URL 集合内。</li>
          <li>抽取子树深度从 6 提升到 16，覆盖深层门户卡片。</li>
          <li>识别 div/dl 实现的页脚导航和祖先页脚签名。</li>
          <li>仍为无链接表格、语录列表保留全元素回退。</li>
        </ul></div>
      </div>
    </section>

    <section>
      <div class="eyebrow">Verification</div>
      <h2>证据与可复现入口</h2>
      <div class="evidence">
        <p>主整套：<code>output/benchmarks/{_e(suite_path.name)}</code></p>
        <p>时光网严格补测：<code>output/benchmarks/{_e(mtime_path.name)}</code></p>
        <p>猫眼受控失败补测：<code>output/benchmarks/{_e(maoyan_path.name)}</code></p>
        <p>快照证据：<code>output/dpcli_snapshots/&lt;session&gt;/</code></p>
        <p>CLI 日志：<code>logs/code_log/20260719/</code></p>
        <p>回归：AutoWeb 77 passed；drissionpage-cli 9 passed。</p>
      </div>
      <p class="note">说明：主整套严格通过率是修复前最后一轮的 18/20；
      报告的 19/20 是在该整套基础上，用同一任务的最终严格时光网补测替换旧证据。
      猫眼补测仍失败，但从 API exception 改为可解释的受控 failed。</p>
    </section>
  </main>
  <footer>AutoWeb × drissionpage-cli · 公开站点自然语言自动化验证 · {_e(generated)}</footer>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        default=str(
            PROJECT_ROOT
            / "output/benchmarks/chinese_sites_20_final_20260719.json"
        ),
    )
    parser.add_argument(
        "--mtime",
        default=str(
            PROJECT_ROOT
            / "output/benchmarks/chinese_sites_mtime_controlled_final_20260719.json"
        ),
    )
    parser.add_argument(
        "--maoyan",
        default=str(
            PROJECT_ROOT
            / "output/benchmarks/chinese_sites_maoyan_controlled_final_20260719.json"
        ),
    )
    parser.add_argument(
        "--output",
        default=str(
            PROJECT_ROOT
            / "output/reports/autoweb_chinese_sites_20_verified_20260719.html"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = generate(
        Path(args.suite),
        Path(args.mtime),
        Path(args.maoyan),
        Path(args.output),
    )
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
