# Core Knowledge Base

LangGraph workflow engine — graph construction, state management, and node implementations.

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Add/modify graph | `graph_v2.py` | `build_graph()` wires nodes; uses `partial()` for DI |
| Change state schema | `state_v2.py` | `AgentState` extends `EnvState` + `TaskState` |
| Node logic | `nodes/` | 15 modules; each node is pure function returning `Command(goto=...)` |
| LLM factory | `llm_factory.py` | `create_llm()` for multi-model setup |

## CONVENTIONS

- **Dynamic routing**: Nodes return `Command(goto="NodeName")` — no explicit edges in graph
- **Dependency injection**: Use `functools.partial()` to bind observer/LLM to nodes
- **State reducers**: `clearable_list_reducer` allows clearing lists with `None`
- **Node signature**: `(state: AgentState, config: RunnableConfig) -> Command`

## ANTI-PATTERNS

- Do NOT add `add_conditional_edges` — use `Command(goto=...)` instead
- Do NOT modify `nodes/` modules without checking token pruning (summarizer at ~1500 tokens)
