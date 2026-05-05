# 2026-05-03 dp_cli 主流程续改计划

## 目标

把已经新增的 dp_cli full snapshot / Agent View / Snapshot Index / TargetSelector 基础设施真正接入 AutoWeb 主运行闭环。

本轮不是继续新增概念，而是解决 review 中暴露的系统割裂问题：

1. TargetSelector 已实现但没有接入 LangGraph。
2. DP Planner 新 prompt 已存在但 planner_node 没有使用。
3. full snapshot 新路径默认关闭，实际运行仍走旧 Observer 兼容路径。
4. TargetSelector 存在一个约束解析优先级 bug。
5. 测试环境和测试入口不可靠，无法证明主流程完成。

最终完成标准：在 `DPCLI_ENABLED=True` 时，系统默认走 `DPObserver -> DPPlanner -> TargetSelector -> ActionBuilder/Coder -> DPExecutor -> DPVerifier` 的 dp_cli 路径；Python 代码生成链路只保留为显式关闭 dp_cli 后的 legacy fallback。

## 本轮任务边界

### 必须做

- 接入 TargetSelector 到 LangGraph 主流程。
- 让 Planner 在 dp_cli 模式下使用 dp_cli 专用 planner prompt。
- 让 Planner 产出结构化 `dpcli_structured_plan`。
- 让 TargetSelector 消费 `dpcli_structured_plan.target_request` 和全量 snapshot/index。
- 让后续 ActionBuilder/Coder 使用 TargetSelector 返回的 `target_ref`。
- 默认启用 full snapshot observer 路径，除非用户显式关闭。
- 修复 TargetSelector 约束解析 bug。
- 补充最小可运行回归测试或 smoke 脚本。
- 确保新测试文件不被 `.gitignore` 忽略。

### 严禁本轮做

- 不要重写整个 `core/nodes/planner.py`。
- 不要删除 legacy Python coder/executor 链路，只能把它降级为非 dp_cli 模式 fallback。
- 不要重构 RAGNode、CacheLookup、Verifier 的整体架构。
- 不要改外部 `drissionpage-cli` 仓库，除非本仓库无法通过适配解决。
- 不要把 full snapshot/index 整体塞进 LLM prompt。
- 不要让 TargetSelector 依赖 LLM 才能做基础确定性选择。
- 不要引入新的大依赖。

## 目标主链路

```text
Observer
  -> CacheLookup
  -> Planner
  -> TargetSelector
  -> Coder
  -> Executor
  -> Verifier
  -> Observer / Planner / RAGNode / END
```

说明：

- `Observer` 在 dp_cli 模式下执行 DPObserver 逻辑。
- `Planner` 在 dp_cli 模式下执行 DPPlanner 逻辑。
- `TargetSelector` 只在当前 step 需要页面目标元素时进入。
- `Coder` 在 dp_cli 模式下不写 Python 代码，只构建 dp_cli action JSON。
- `Executor` 继续通过 `skills/dpcli_executor.py` 执行 dp_cli。
- `RAGNode`、`CacheLookup`、`ErrorHandler` 保留，不要丢弃。

## P0：修复 TargetSelector 明确 bug

文件：

- `core/nodes/target_selector.py`

修改：

```python
text_hints = constraints.get("text_or_name") or ([target_hint] if target_hint else [])
```

同时补一个单元测试：

- 当 `target_hint=""` 且 `constraints={"text_or_name": ["搜索"]}` 时，TargetSelector 必须仍然使用 `"搜索"` 检索。

验收：

- 该测试通过。
- 不影响已有 select 返回格式。

## P1：默认启用 full snapshot observer 路径

文件：

- `config.py`
- `core/nodes/_dpcli.py`

建议修改：

```python
DPCLI_FULL_SNAPSHOT_MODE = _env_bool("DPCLI_FULL_SNAPSHOT_MODE", "True")
```

注意：

- 保留环境变量开关，允许用户临时回退。
- 如果 full snapshot 构建失败，应记录 `dpcli_observer_diagnostics.errors`，然后降级到 legacy snapshot view，而不是直接中断任务。

验收：

- 不设置环境变量时，dp_cli observer 默认写入：
  - `dpcli_snapshot_ref`
  - `dpcli_agent_view`
  - `dpcli_snapshot_index`
  - `dpcli_observer_diagnostics`
- `dom_skeleton` 可以继续写兼容 JSON 字符串，但不得成为 dp_cli planner 的主要输入。

## P2：接入 TargetSelector 到 LangGraph

文件：

- `core/graph_v2.py`
- `core/nodes/__init__.py`
- `core/nodes/target_selector.py`

需要做：

1. 在 `core/nodes/target_selector.py` 中提供 LangGraph node 函数，例如：

```python
def target_selector_node(state: AgentState) -> Command:
    ...
```

2. 在 `core/nodes/__init__.py` 导出 `target_selector_node`。

3. 在 `core/graph_v2.py` 注册：

```python
workflow.add_node("TargetSelector", target_selector_node)
```

4. 不要新增 `add_conditional_edges`，继续遵守项目约定：节点内部返回 `Command(goto="...")`。

TargetSelector node 路由规则：

- 成功选中目标：更新 `dpcli_target_result`，`goto="Coder"`。
- 当前 step 不需要目标：写入 `dpcli_target_result={"status": "not_required"}`，`goto="Coder"`。
- 候选冲突或低置信度：`goto="Executor"` 前不允许继续，应该进入现有 HITL/approval 机制；若当前没有 approval node，则先 `goto="Planner"` 并在 state 写入明确的 `human_approval_required` / `execution_result` 提示。
- full snapshot 不可用：`goto="Observer"` 重新观察一次；二次失败后 `goto="ErrorHandler"`。

验收：

- `DPCLI_ENABLED=True` 且 Planner 产出需要点击/输入/选择目标时，图会进入 TargetSelector。
- `DPCLI_ENABLED=False` 时不影响旧流程。

## P3：让 Planner 使用 dp_cli 专用 prompt

文件：

- `core/nodes/planner.py`
- `core/nodes/_dpcli.py`
- `prompts/dpcli_planner_prompts.py`

需要做：

1. 在 `planner_node` 内按 `execution_mode == "dp_cli"` 或 `DPCLI_ENABLED` 分支。
2. dp_cli 分支不要继续使用旧 `PLANNER_STEP_PROMPT`。
3. dp_cli 分支调用 `_dpcli_planner_context(state)` 或等价函数，输入必须来自：
   - `user_task`
   - `current_url`
   - `previous_steps`
   - `dpcli_agent_view`
   - `dpcli_observer_diagnostics`
   - `execution_result`
   - `verification_result`
   - `rag_result`
4. LLM 输出必须解析为结构化 JSON，写入 `dpcli_structured_plan`。

建议 `dpcli_structured_plan` 最小结构：

```json
{
  "step_intent": "click | input | extract | scroll | wait | navigate | finish",
  "target_request": {
    "required": true,
    "target_hint": "搜索按钮",
    "role": "button",
    "text_or_name": ["搜索"],
    "region_hint": "search_area",
    "constraints": {}
  },
  "action_payload": {
    "text": "",
    "url": "",
    "direction": ""
  },
  "reason": "为什么下一步这么做",
  "needs_rag": false,
  "needs_human_approval": false
}
```

Planner 路由规则：

- `needs_rag=True`：`goto="RAGNode"`。
- `step_intent="finish"`：`goto="Verifier"`。
- `target_request.required=True`：`goto="TargetSelector"`。
- 不需要目标元素的动作：`goto="Coder"`。

验收：

- dp_cli 模式下 Planner 不再依赖 `locator_suggestions` 做主要决策。
- Planner 能用 `dpcli_agent_view.top_level_groups`、`data_regions`、`pagination` 判断页面能力。

## P4：让 Coder/ActionBuilder 使用 `target_ref`

文件：

- `core/nodes/coder.py`
- `core/nodes/_dpcli.py`
- `prompts/dpcli_action_prompts.py`

需要做：

1. `_dpcli_action_context(state)` 必须加入：
   - `dpcli_structured_plan`
   - `dpcli_target_result`
   - `dpcli_snapshot_ref`
   - 必要的候选解释，不要塞 full snapshot
2. dp_cli action 生成时优先使用：

```json
{
  "target_ref": "..."
}
```

3. 如果 `dpcli_target_result.status != "selected"` 且动作需要目标，不允许生成猜测型 action。

验收：

- click/input/select 类型 action 必须带 `target_ref` 或进入 approval/error。
- 不再让 Coder 从自然语言计划里自己猜 selector。

## P5：整理 snapshot index 构建质量

文件：

- `skills/dpcli_snapshot_indexer.py`
- `skills/dpcli_snapshot_query.py`
- `skills/dpcli_planner_view.py`

需要做：

1. 去重：`interactable_elements + surface_index + deep_index` 可能有重复 ref。应以信息更完整的 node 覆盖摘要 node。
2. 中文检索：`find_by_text()` 不应只依赖 `split()` token。至少增加 substring fallback。
3. structural hash：不要依赖不存在的 `children_map` 字段。可以从 `by_parent` 或 snapshot 原始节点关系构造 child role/tag signature。
4. search area 检测：role 为 `searchbox` 的输入，即使没有 placeholder/name，也应进入搜索区域候选。

验收：

- 相同 ref 不会被低信息摘要覆盖。
- 中文 `"搜索按钮"` 能命中 name/text 包含 `"搜索"` 的节点。
- repeated group 压缩不会把不同语义区域误合并。

## P6：测试与可运行性

当前问题：

- `test/test_dpcli_observer_target_selector.py` 被 `.gitignore` 忽略。
- 默认环境缺 `tiktoken`。
- `dp-cli` conda 环境缺 `langgraph`。

可选解决方式：

1. 将新测试移动到 `tests/`，避免被 `.gitignore` 的 `test` 规则吞掉。
2. 对纯函数测试避免 import `core.state_v2` 这类会拉起 `langgraph`/LLM 依赖的模块。
3. smoke 脚本放 `scripts/`，只测 snapshot indexer/query/selector 的确定性逻辑。

最低验收命令：

```bash
python -m py_compile config.py core/graph_v2.py core/nodes/_dpcli.py core/nodes/planner.py core/nodes/coder.py core/nodes/target_selector.py skills/dpcli_snapshot_indexer.py skills/dpcli_snapshot_query.py skills/dpcli_planner_view.py skills/dpcli_snapshot_store.py
python scripts/test_dpcli_observer_target_selector.py
```

如果当前 Python 环境依赖不完整，至少要提供一个不依赖 LangGraph/tiktoken 的纯函数 smoke：

```bash
python scripts/smoke_dpcli_snapshot_selector.py
```

## P7：最小端到端 smoke

新增脚本建议：

- `scripts/smoke_dpcli_flow_plan_target.py`

目标：

- 不真实启动浏览器。
- 使用 fixture snapshot。
- 模拟 state：
  - `DPCLI_ENABLED=True`
  - 有 `dpcli_agent_view`
  - 有 `dpcli_snapshot_index`
  - planner 输出一个 click 搜索按钮的 `dpcli_structured_plan`
- 调用 TargetSelector node。
- 调用 dp_cli action context builder。

验收：

- TargetSelector 返回 selected。
- action context 中包含 selected `target_ref`。
- 不生成 Python code。

## 推荐提交顺序

### Commit 1：修复 selector 与测试可运行性

- 修复 TargetSelector 优先级 bug。
- 增加纯函数测试。
- 处理测试文件被忽略问题。

### Commit 2：Observer full snapshot 默认启用

- `DPCLI_FULL_SNAPSHOT_MODE=True`。
- full snapshot fallback diagnostics。
- index 去重和中文检索增强。

### Commit 3：接入 TargetSelector graph node

- 新增 `target_selector_node`。
- graph 注册。
- nodes init 导出。

### Commit 4：Planner dp_cli 结构化输出

- Planner dp_cli 分支接 prompt。
- 写入 `dpcli_structured_plan`。
- 正确路由 TargetSelector/Coder/RAG/Verifier。

### Commit 5：Coder 使用 target_ref 构建 dp_cli action

- `_dpcli_action_context` 加入 structured plan + target result。
- action prompt 限制不能猜目标。
- 缺 target_ref 时进入错误/审批。

## 最终验收清单

- `DPCLI_ENABLED=True` 默认进入 dp_cli 执行模式。
- Observer 默认生成 full snapshot artifacts。
- Planner 默认消费 `dpcli_agent_view`，而不是旧 locator suggestions。
- Planner 输出 `dpcli_structured_plan`。
- Graph 中实际存在 TargetSelector 节点。
- 需要目标元素的动作一定经过 TargetSelector。
- Coder 不再写 Python 代码。
- dp_cli action 里优先使用确定的 `target_ref`。
- RAGNode、CacheLookup、Verifier 没有被移除。
- 测试或 smoke 能在当前本地环境跑通。

## 本轮完成后仍然暂缓的后续接口

这些接口要预留，但不要在本轮展开：

- 多候选 human approval UI。
- TargetSelector LLM 仲裁器。
- dp_cli `expand/find/list-items` 的高级 agentic search。
- 跨页面 snapshot 历史索引。
- 成功 action 写回 ActionCache。
- 失败 action blacklist 与 selector 负反馈学习。

## 对 opencode 的一句话指令

请优先把已新增的 dp_cli snapshot/index/view/selector 基础设施接入主 LangGraph 执行闭环，不要继续扩展新概念；先修 blocker、接主链路、保证 dp_cli 模式默认可走通，再做索引质量优化和 smoke 测试。
