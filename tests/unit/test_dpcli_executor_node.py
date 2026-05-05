import unittest
import sys
import types
from unittest.mock import patch

sys.modules.setdefault("tiktoken", types.SimpleNamespace())

messages_mod = types.ModuleType("langchain_core.messages")


class _Message:
    def __init__(self, content=""):
        self.content = content


messages_mod.HumanMessage = _Message
messages_mod.AIMessage = _Message
messages_mod.RemoveMessage = _Message
messages_mod.BaseMessage = _Message
sys.modules.setdefault("langchain_core", types.ModuleType("langchain_core"))
sys.modules.setdefault("langchain_core.messages", messages_mod)

runnables_mod = types.ModuleType("langchain_core.runnables")
runnables_mod.RunnableConfig = dict
sys.modules.setdefault("langchain_core.runnables", runnables_mod)

langgraph_types_mod = types.ModuleType("langgraph.types")


class _Command:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, update=None, goto=None):
        self.update = update or {}
        self.goto = goto


langgraph_types_mod.Command = _Command
sys.modules.setdefault("langgraph", types.ModuleType("langgraph"))
sys.modules.setdefault("langgraph.types", langgraph_types_mod)

langgraph_message_mod = types.ModuleType("langgraph.graph.message")
langgraph_message_mod.add_messages = lambda existing, update: (existing or []) + (update or [])
sys.modules.setdefault("langgraph.graph", types.ModuleType("langgraph.graph"))
sys.modules.setdefault("langgraph.graph.message", langgraph_message_mod)

actor_mod = types.ModuleType("skills.actor")
actor_mod.BrowserActor = object
sys.modules.setdefault("skills.actor", actor_mod)

logger_mod = types.ModuleType("skills.logger")
logger_mod.logger = types.SimpleNamespace(
    info=lambda *args, **kwargs: None,
    warning=lambda *args, **kwargs: None,
    debug=lambda *args, **kwargs: None,
    error=lambda *args, **kwargs: None,
)
sys.modules.setdefault("skills.logger", logger_mod)

coder_prompts_mod = types.ModuleType("prompts.coder_prompts")
coder_prompts_mod.ACTION_CODE_GEN_PROMPT = "{xpath_plan}"
coder_prompts_mod.CODER_TASK_WRAPPER = "{plan}\n{base_prompt}"
sys.modules.setdefault("prompts.coder_prompts", coder_prompts_mod)

planner_prompts_mod = types.ModuleType("prompts.planner_prompts")
planner_prompts_mod.PLANNER_START_PROMPT = ""
planner_prompts_mod.PLANNER_STEP_PROMPT = ""
planner_prompts_mod.PLANNER_CONTINUE_PROMPT = ""
planner_prompts_mod.PLANNER_FORCE_SKIP_PROMPT = ""
sys.modules.setdefault("prompts.planner_prompts", planner_prompts_mod)

verifier_prompts_mod = types.ModuleType("prompts.verifier_prompts")
verifier_prompts_mod.VERIFIER_CHECK_PROMPT = "{user_task}{current_plan}{current_url}{log}"
verifier_prompts_mod.ERROR_RECOVERY_PROMPT = ""
sys.modules.setdefault("prompts.verifier_prompts", verifier_prompts_mod)

from core.nodes import _executor_dpcli_branch


class DPCLIExecutorNodeTests(unittest.TestCase):
    def test_dpcli_success_goes_to_verifier(self):
        state = {
            "generated_action": {"skill": "click", "params": {"ref": "e1"}},
            "dpcli_session": "unit",
            "current_url": "https://example.test",
        }
        result_payload = {
            "ok": True,
            "session": "unit",
            "action": "click",
            "data": {"page": {"url": "https://example.test/next"}},
            "error": None,
        }
        with patch("skills.dpcli_executor.DPCLIExecutor") as executor_cls:
            executor_cls.return_value.execute_action.return_value = result_payload
            command = _executor_dpcli_branch(state, {"configurable": {}})

        self.assertEqual(command.goto, "Verifier")
        self.assertEqual(command.update["dpcli_result"], result_payload)
        self.assertEqual(command.update["current_url"], "https://example.test/next")
        executor_cls.return_value.execute_action.assert_called_once_with(state["generated_action"])

    def test_ref_stale_goes_to_observer(self):
        state = {
            "generated_action": {"skill": "click", "params": {"ref": "e1"}},
            "dpcli_session": "unit",
        }
        result_payload = {
            "ok": False,
            "session": "unit",
            "action": "click",
            "data": None,
            "error": {"code": "ref_stale", "message": "stale", "details": {"ref": "e1"}},
        }
        with patch("skills.dpcli_executor.DPCLIExecutor") as executor_cls:
            executor_cls.return_value.execute_action.return_value = result_payload
            command = _executor_dpcli_branch(state, {"configurable": {}})

        self.assertEqual(command.goto, "Observer")
        self.assertEqual(command.update["error_type"], "dpcli_ref_stale")
        self.assertFalse(command.update["verification_result"]["is_success"])

    def test_invalid_action_goes_to_coder(self):
        command = _executor_dpcli_branch({"generated_action": None}, {"configurable": {}})

        self.assertEqual(command.goto, "Coder")
        self.assertEqual(command.update["error_type"], "dpcli_invalid_action")


if __name__ == "__main__":
    unittest.main()
