from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.nodes._dpcli import _dpcli_planner_context
from core.nodes.planner import _apply_irreversible_action_boundary
from core.nodes.planner import planner_node
from core.nodes.skill_selector import skill_selector_node
from skills.agent_skill_runtime import (
    AgentSkillRegistry,
    SkillValidationError,
    parse_skill_selection,
    skill_selection_required,
)
from skills.safety_boundaries import irreversible_target


def _write_skill(root: Path, name: str, description: str, body: str) -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def test_registry_catalog_contains_only_name_and_description(tmp_path):
    _write_skill(
        tmp_path,
        "maoyan-movie",
        "Use for Maoyan movie pages.",
        "SECRET_BODY_MARKER",
    )
    registry = AgentSkillRegistry(tmp_path)

    assert registry.catalog() == [{
        "name": "maoyan-movie",
        "description": "Use for Maoyan movie pages.",
    }]
    assert "SECRET_BODY_MARKER" not in str(registry.catalog())


def test_load_selected_reads_only_selected_skill_bodies(tmp_path):
    _write_skill(tmp_path, "alpha-skill", "Alpha metadata.", "ALPHA_BODY")
    _write_skill(tmp_path, "beta-skill", "Beta metadata.", "BETA_BODY")
    registry = AgentSkillRegistry(tmp_path)

    loaded = registry.load_selected(["beta-skill"], max_selected=2)

    assert [item.metadata.name for item in loaded] == ["beta-skill"]
    assert loaded[0].body == "BETA_BODY"


def test_selection_parser_validates_names_deduplicates_and_caps_count():
    result = parse_skill_selection(
        '{"selected_skills":["alpha-skill","missing","alpha-skill","beta-skill"],'
        '"reason":"domain match"}',
        ["alpha-skill", "beta-skill"],
        max_selected=1,
    )

    assert result.selected_names == ("alpha-skill",)
    assert result.invalid_names == ("missing",)
    assert result.reason == "domain match"


def test_selector_llm_sees_metadata_then_only_selected_body_is_loaded(
    tmp_path, monkeypatch
):
    _write_skill(tmp_path, "alpha-skill", "Alpha pages.", "ALPHA_BODY_MARKER")
    _write_skill(tmp_path, "beta-skill", "Beta pages.", "BETA_BODY_MARKER")
    registry = AgentSkillRegistry(tmp_path)
    captured = {}

    def fake_invoke(llm, messages, **kwargs):
        captured["prompt"] = messages[0].content
        return SimpleNamespace(
            content='{"selected_skills":["alpha-skill"],"reason":"matches task"}'
        )

    monkeypatch.setattr("core.nodes.skill_selector.traced_llm_invoke", fake_invoke)
    command = skill_selector_node(
        {
            "user_task": "open alpha",
            "current_url": "https://alpha.example/",
        },
        {"configurable": {"thread_id": "skill-test"}},
        llm=object(),
        registry=registry,
    )

    assert "Alpha pages." in captured["prompt"]
    assert "Beta pages." in captured["prompt"]
    assert "ALPHA_BODY_MARKER" not in captured["prompt"]
    assert "BETA_BODY_MARKER" not in captured["prompt"]
    assert command.update["active_skill_names"] == ["alpha-skill"]
    assert "ALPHA_BODY_MARKER" in command.update["active_skill_context"]
    assert "BETA_BODY_MARKER" not in command.update["active_skill_context"]
    assert command.goto == "Planner"


def test_selection_key_reloads_on_domain_or_catalog_change(tmp_path):
    _write_skill(tmp_path, "alpha-skill", "Alpha pages.", "BODY")
    registry = AgentSkillRegistry(tmp_path)
    state = {"user_task": "cross site", "skill_selection_key": ""}

    assert skill_selection_required(state, "https://a.example/", registry)
    key = registry.selection_key("cross site", "https://a.example/")
    state["skill_selection_key"] = key
    assert not skill_selection_required(state, "https://a.example/path", registry)
    assert skill_selection_required(state, "https://b.example/", registry)

    _write_skill(tmp_path, "beta-skill", "Beta pages.", "BODY")
    assert skill_selection_required(state, "https://a.example/path", registry)


def test_planner_routes_to_metadata_selector_before_planning():
    class ExplodingLLM:
        def invoke(self, _messages):
            raise AssertionError("Planner must not run before SkillSelector")

    command = planner_node(
        {
            "user_task": "在猫眼搜索电影",
            "current_url": "https://www.maoyan.com/films",
            "loop_count": 0,
            "finished_steps": [],
            "verification_result": {},
            "skill_selection_key": None,
        },
        {"configurable": {"browser": None}},
        ExplodingLLM(),
    )

    assert command.goto == "SkillSelector"
    assert command.update["current_url"] == "https://www.maoyan.com/films"


def test_registry_rejects_nonstandard_frontmatter(tmp_path):
    path = _write_skill(tmp_path, "alpha-skill", "Alpha pages.", "BODY")
    path.write_text(
        "---\nname: alpha-skill\ndescription: Alpha pages.\ndomains: example.com\n---\nBODY\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillValidationError, match="unsupported frontmatter"):
        AgentSkillRegistry(tmp_path).discover()


def test_repository_skill_catalog_is_valid_and_progressively_loadable():
    root = Path(__file__).resolve().parents[2] / "agent_skills"
    registry = AgentSkillRegistry(root)
    metadata = registry.discover()

    assert len(metadata) >= 20
    assert {"iiice-portal", "maoyan-movie", "steam-store"}.issubset(
        {item.name for item in metadata}
    )
    for item in metadata:
        loaded = registry.load_selected([item.name], max_selected=1)
        assert loaded and loaded[0].body.startswith("#")


def test_dpcli_planner_context_receives_only_active_skill_context():
    context = _dpcli_planner_context({
        "dpcli_agent_view": {
            "page": {"url": "https://www.maoyan.com/films"},
            "capability_map": {},
        },
        "current_url": "https://www.maoyan.com/films",
        "user_task": "搜索电影",
        "finished_steps": [],
        "reflections": [],
        "loop_count": 0,
        "execution_mode": "dp_cli",
        "active_skill_context": '<agent_skills><skill name="maoyan-movie">BODY</skill></agent_skills>',
    })

    assert '<skill name="maoyan-movie">BODY</skill>' in context
    assert "jd-commerce" not in context


def test_irreversible_actions_remain_deterministic_not_skill_controlled():
    assert irreversible_target("点击提交订单") == "提交订单"
    assert irreversible_target("Pay now") == "pay now"
    assert irreversible_target("打开商品详情") is None

    plan = _apply_irreversible_action_boundary({
        "step_intent": "click",
        "target_request": {
            "target_hint": "红色的提交订单按钮",
            "text_or_name": ["提交订单"],
        },
        "reason": "完成购买",
    })
    assert plan["step_intent"] == "finish"
    assert plan["safe_stop"] is True
    assert plan["blocked_action"] == "提交订单"
