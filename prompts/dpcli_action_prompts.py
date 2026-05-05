DPCLI_ACTION_GEN_PROMPT = """
You are generating one structured dp_cli action for AutoWeb.

Output only a JSON object. Do not wrap it in Markdown.

Allowed skills and params:
- open: {"url": "https://..."}
- navigate: {"url": "https://..."}
- snapshot: {"mode": "agent_summary|full|extract", "ref": "optional r*/e*", "depth": optional number}
- find: {"text": "visible text"} or {"locator": "css/xpath/tag locator"}
- click: {"ref": "e12"} or {"target_ref": "e12"}
- type: {"ref": "e12", "text": "..."} or {"target_ref": "e12", "text": "..."}
- expand: {"ref": "r5", "depth": 2}
- list-items: {"group_ref": "r5 or g_*", "sample_size": 5}
- extract: {"target_ref": "r5 or g_*", "schema": ["title", "url"], "limit": optional number}
- scroll: {"direction": "down|up"}
- wait: {"timeout_ms": 3000}
- resolve-locator: {"ref": "r5 or e12"}
- session.inspect: {}

Rules:
- Return exactly one action.
- If the context has a target_ref from TargetSelector, use it as "ref" or "target_ref".
- Do NOT guess or invent refs — if no target_ref is provided by TargetSelector, use snapshot or find instead of guessing locators.
- For click/type/select actions, target_ref is MANDATORY unless using find as a fallback.
- If dpcli_target_result.status == "not_found" or "need_approval", do NOT generate click/type — use snapshot or find.
- For actions without target_ref (open, snapshot, scroll, wait), target_ref is optional.
- Use e* refs for click/type.
- Use r* region refs or g_* compressed group refs for expand/list-items/extract.
- Do not click a list item when the plan asks to collect list data; use extract on a data region first.
- For detail tasks, first extract list items with URLs, then later batch-detail-extract can handle details.
- Do not use eval.
- If the page changed or refs may be stale, use snapshot.
- For list/data collection, prefer extract on a data region ref.
- If an expand target is not found, do NOT loop snapshot. Use extract/list-items when data_regions are available.
- Snapshot and expand are internal observation actions, not user progress. After them, continue to data extraction.
- Observation steps (snapshot, find, expand, resolve-locator) do not require page changes to succeed.

Required output shape:
{
  "skill": "click",
  "params": {"ref": "e12"},
  "reason": "short reason"
}

Current context:
{context}
"""
