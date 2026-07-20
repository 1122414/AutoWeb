"""Generate an offline HTML report for one AutoWeb Run Trace."""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skills.run_trace import RunTraceStore


def _escape(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def build_report(summary: dict, events: list[dict]) -> str:
    rows = []
    for event in events:
        token_label = (
            f"{event['total_tokens']:,}"
            + ("（估算）" if event.get("estimated_tokens") else "")
        )
        rows.append(
            f"""
            <tr>
              <td>{_escape(event.get("node"))}</td>
              <td>{_escape(event.get("event_type"))}</td>
              <td>{_escape(event.get("model"))}</td>
              <td>{token_label}</td>
              <td>{float(event.get("duration_ms") or 0):.1f} ms</td>
              <td><code>{_escape(event.get("payload"))}</code></td>
            </tr>
            """
        )
    exact = int(summary.get("estimated_call_count") or 0) == 0
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>AutoWeb Run Trace</title>
  <style>
    body {{ margin:0; color:#14233b; background:#f2f6f7; font:15px/1.55 system-ui,"Microsoft YaHei"; }}
    header,main {{ max-width:1120px; margin:auto; padding:34px 22px; }}
    header {{ color:white; max-width:none; background:linear-gradient(125deg,#071a30,#124d57); }}
    header div {{ max-width:1120px; margin:auto; }}
    h1 {{ font-size:42px; margin:6px 0; }} .muted {{ color:#6d7b8d; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }}
    .metric,.panel {{ background:white; border:1px solid #dfe7ea; border-radius:14px; padding:18px; }}
    .metric b {{ display:block; font-size:27px; color:#008c79; }}
    .metric span {{ color:#6d7b8d; }} .panel {{ margin-top:20px; overflow:auto; }}
    table {{ width:100%; border-collapse:collapse; }} th,td {{ padding:11px; border-bottom:1px solid #e5ebed; text-align:left; }}
    th {{ color:#64748b; }} code {{ overflow-wrap:anywhere; }}
    @media(max-width:700px) {{ .grid {{ grid-template-columns:1fr 1fr; }} }}
  </style>
</head>
<body>
  <header><div><small>AUTOWEB · RUN TRACE</small><h1>Token、成本与动作轨迹</h1>
    <p>Thread {_escape(summary.get("thread_id"))} · {"全部为模型返回的精确 usage" if exact else "含明确标记的 Token 估算"}</p></div></header>
  <main>
    <section class="grid">
      <div class="metric"><b>{int(summary.get("total_tokens") or 0):,}</b><span>总 Token</span></div>
      <div class="metric"><b>{int(summary.get("llm_call_count") or 0)}</b><span>模型调用</span></div>
      <div class="metric"><b>{int(summary.get("browser_action_count") or 0)}</b><span>浏览器动作</span></div>
      <div class="metric"><b>${float(summary.get("cost_usd") or 0):.6f}</b><span>配置价格下成本</span></div>
    </section>
    <section class="panel"><h2>事件明细</h2>
      <table><thead><tr><th>节点</th><th>类型</th><th>模型</th><th>Token</th><th>耗时</th><th>证据</th></tr></thead>
      <tbody>{"".join(rows)}</tbody></table>
    </section>
    <p class="muted">输入 Token：{int(summary.get("input_tokens") or 0):,} · 输出 Token：{int(summary.get("output_tokens") or 0):,} · 估算调用：{int(summary.get("estimated_call_count") or 0)}</p>
  </main>
</body>
</html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    store = RunTraceStore(args.db)
    report = build_report(
        store.summary_dict(args.thread_id),
        store.events(args.thread_id),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
