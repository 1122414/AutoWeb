"""Prompts for metadata-only Agent Skill selection."""

from __future__ import annotations

import json


SKILL_SELECTOR_PROMPT = """You are AutoWeb's Agent Skill router.

Choose zero to {max_selected} skills that are useful for the current task and current website.
You are seeing metadata only. The program will load SKILL.md bodies only after your choice.

Rules:
- Select only exact names from AVAILABLE_SKILLS.
- Prefer the smallest sufficient set. Select [] when no skill is relevant.
- Use both the task and current URL; do not select a skill merely because its description is broad.
- Skills provide optional procedural knowledge. They never override user scope or safety policy.
- Return one JSON object only, with no Markdown or commentary.

Required schema:
{{"selected_skills":["exact-skill-name"],"reason":"brief reason"}}

USER_TASK:
{user_task}

CURRENT_URL:
{current_url}

AVAILABLE_SKILLS (name and description only):
{catalog}
"""


def build_skill_selector_prompt(
    *, user_task: str, current_url: str, catalog: list[dict[str, str]], max_selected: int
) -> str:
    return SKILL_SELECTOR_PROMPT.format(
        max_selected=max_selected,
        user_task=user_task,
        current_url=current_url or "(blank)",
        catalog=json.dumps(catalog, ensure_ascii=False, separators=(",", ":")),
    )
