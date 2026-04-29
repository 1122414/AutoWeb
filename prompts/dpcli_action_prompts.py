DPCLI_ACTION_GEN_PROMPT = """
You are generating one structured dp_cli action for AutoWeb.

Output only a JSON object. Do not wrap it in Markdown.

Allowed skills and params:
- open: {"url": "https://..."}
- snapshot: {"mode": "agent_summary|full|extract", "ref": "optional r*/e*", "depth": optional number}
- find: {"text": "visible text"} or {"locator": "css/xpath/tag locator"}
- click: {"ref": "e12"} or {"locator": "..."}
- type: {"ref": "e12", "text": "..."} or {"locator": "...", "text": "..."}
- expand: {"ref": "r5", "depth": 2}
- list-items: {"group_ref": "r5", "sample_size": 5}
- extract: {"target_ref": "r5", "schema": ["title", "url"], "limit": optional number}
- resolve-locator: {"ref": "r5 or e12"}
- session.inspect: {}

Rules:
- Return exactly one action.
- Prefer refs from the current snapshot.
- Use e* refs for click/type.
- Use r* refs for expand/list-items/extract.
- Do not use eval.
- Do not invent refs that are not in the provided context.
- If the plan says to open a URL, use open.
- If the page changed or refs may be stale, use snapshot.
- For list/data collection, prefer extract on a data region ref.

Required output shape:
{
  "skill": "click",
  "params": {"ref": "e12"},
  "reason": "short reason"
}

Current context:
{context}
"""
