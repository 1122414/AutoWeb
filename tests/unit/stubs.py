"""Shared lightweight dependency stubs for unit tests.

Import this module to install mock replacements for langchain, tiktoken,
and other heavy dependencies that are not needed in pure unit tests.
No side effects beyond sys.modules patching.
"""
import importlib.util
import sys
import types

sys.modules.setdefault("tiktoken", types.SimpleNamespace())

_graph_stack_available = (
    importlib.util.find_spec("langchain_core") is not None
    and importlib.util.find_spec("langgraph") is not None
)

if not _graph_stack_available:
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
logger_mod.trace_log = lambda *args, **kwargs: None
logger_mod.save_dpcli_code_log = lambda *args, **kwargs: None
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
