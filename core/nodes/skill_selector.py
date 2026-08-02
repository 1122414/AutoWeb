"""LangGraph node for LLM-selected, progressively loaded Agent Skills."""

from __future__ import annotations

from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from core.state_v2 import AgentState
from prompts.skill_selector_prompts import build_skill_selector_prompt
from skills.agent_skill_runtime import (
    AgentSkillRegistry,
    get_default_skill_registry,
    parse_skill_selection,
    render_loaded_skills,
)
from skills.logger import logger
from skills.run_trace import traced_llm_invoke


def skill_selector_node(
    state: AgentState,
    config: RunnableConfig,
    llm,
    registry: AgentSkillRegistry | None = None,
) -> Command[Literal["Planner"]]:
    """Expose metadata to the LLM, then load only the selected skill bodies."""

    from config import AGENT_SKILLS_ENABLED, AGENT_SKILLS_MAX_SELECTED

    active_registry = registry or get_default_skill_registry()
    current_url = str(state.get("current_url") or "")
    selection_key = active_registry.selection_key(
        str(state.get("user_task") or ""), current_url
    )
    catalog = active_registry.catalog()
    if not AGENT_SKILLS_ENABLED or not catalog:
        return Command(
            update={
                "skill_selection_key": selection_key,
                "active_skill_names": [],
                "active_skill_context": "",
                "skill_selection": {
                    "selected_skills": [],
                    "reason": "skill runtime disabled or catalog empty",
                    "catalog_size": len(catalog),
                },
            },
            goto="Planner",
        )

    prompt = build_skill_selector_prompt(
        user_task=str(state.get("user_task") or ""),
        current_url=current_url,
        catalog=catalog,
        max_selected=AGENT_SKILLS_MAX_SELECTED,
    )
    try:
        response = traced_llm_invoke(
            llm,
            [HumanMessage(content=prompt)],
            node="SkillSelector",
            state=state,
            config=config,
        )
        selection = parse_skill_selection(
            str(getattr(response, "content", "") or ""),
            [item["name"] for item in catalog],
            max_selected=AGENT_SKILLS_MAX_SELECTED,
        )
        loaded = active_registry.load_selected(
            selection.selected_names,
            max_selected=AGENT_SKILLS_MAX_SELECTED,
        )
        names = [item.metadata.name for item in loaded]
        context = render_loaded_skills(loaded)
        reason = selection.reason
        invalid_names = list(selection.invalid_names)
        error = ""
    except Exception as exc:
        logger.warning(f"[SkillSelector] selection failed; continuing without skills: {exc}")
        names = []
        context = ""
        reason = "skill selection failed; planner fallback remains available"
        invalid_names = []
        error = f"{type(exc).__name__}: {exc}"

    logger.info(
        f"   🧩 [SkillSelector] selected={names or ['(none)']} catalog={len(catalog)}"
    )
    audit = {
        "selected_skills": names,
        "reason": reason,
        "invalid_skills": invalid_names,
        "catalog_size": len(catalog),
        "selection_key": selection_key,
        "current_url": current_url,
    }
    if error:
        audit["error"] = error
    return Command(
        update={
            "messages": [
                AIMessage(
                    content=(
                        "[Agent Skill selection] "
                        + (", ".join(names) if names else "none")
                    )
                )
            ],
            "skill_selection_key": selection_key,
            "active_skill_names": names,
            "active_skill_context": context,
            "skill_selection": audit,
        },
        goto="Planner",
    )
