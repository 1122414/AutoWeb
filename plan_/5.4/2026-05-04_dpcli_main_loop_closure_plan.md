# 2026-05-04 dp_cli 主闭环收口修复计划

## 背景

上一轮 opencode 已经把 TargetSelector、dp_cli Planner prompt、full snapshot 默认开关等主干部件接入了一部分，但 review 后确认系统仍不能算完成。当前问题不是缺新概念，而是已有部件之间还有断点，导致 dp_cli 路径仍可能：

- Planner 给了结构化目标信息，但 TargetSelector 没收到完整约束。
- Coder 失败后重新回退到 Python 代码生成。
- prompt 允许的 dp_cli action 与 validator 不一致。
- 首轮已有页面仍可能绕过 dp_cli structured Planner。
- smoke 脚本仍无法在本地环境证明 Plan -> Target -> Action 闭环。

本计划目标是把这些断点收口，让系统在 `DPCLI_ENABLED=True` 时稳定进入 dp_cli 主路径，并且能用本地 smoke 证明。

## 总目标

在 `DPCLI_ENABLED=True` 且 `execution_mode != "python_code"` 时，系统必须默认使用以下链路：

```text
Observer
  -> Planner(dp_cli structured)
  -> TargetSelector
  -> Coder(dp_cli action JSON)
  -> Executor(dp_cli)
  -> Verifier
```

旧 Python Coder 只能作为显式关闭 dp_cli 后的 legacy fallback，不能在 dp_cli action 生成失败时自动进入。

## 本轮任务边界

### 必须做

1. 修复 Planner -> TargetSelector 的 target_request 字段映射。
2. 禁止 dp_cli action 失败后自动回退 Python Coder。
3. 统一 `find` action 的 prompt、validator、executor 语义。
4. 将 dp_cli Planner 分支前置，避免首轮已有页面走旧 Planner。
5. 修复 smoke 脚本导入路径，使其不因 `tiktoken` 缺失而失败。
6. 补充或更新测试，证明上述 5 点。

### 严禁本轮做

- 不要继续新增大节点或新架构名词。
- 不要重写 LangGraph 整体结构。
- 不要删除 RAGNode、CacheLookup、Verifier、ErrorHandler。
- 不要改外部 `drissionpage-cli` 仓库。
- 不要把 full snapshot 整包塞给 LLM。
- 不要把 dp_cli action 失败静默降级到 Python。
- 不要用安装依赖掩盖 smoke 脚本设计问题；smoke 应尽量只测本轮纯逻辑。

## P0：修复 Planner 到 TargetSelector 的约束映射

### 问题

文件：

- `core/nodes/target_selector.py`

当前位置大致在 `target_selector_node()`：

```python
result = selector.select(
    query={
        "intent": intent,
        "target_hint": target_request.get("target_hint", ""),
        "target_constraints": target_request.get("constraints", {}),
    },
    snapshot_ref=snapshot_ref,
)
```

这里只传了 `target_request.constraints`。但 dp_cli Planner prompt 设计中，`role`、`text_or_name`、`region_hint` 是 `target_request` 的一级字段：

```json
{
  "target_request": {
    "required": true,
    "target_hint": "搜索按钮",
    "role": "button",
    "text_or_name": ["搜索"],
    "region_hint": "search_area",
    "constraints": {}
  }
}
```

所以 TargetSelector 收不到最重要的 `role` 和 `text_or_name`。

### 修改要求

新增一个本地 helper，例如：

```python
def _normalize_target_constraints(target_request: Dict[str, Any]) -> Dict[str, Any]:
    constraints = dict(target_request.get("constraints") or {})

    role = target_request.get("role")
    if role and "role" not in constraints:
        constraints["role"] = role if isinstance(role, list) else [role]

    text_or_name = target_request.get("text_or_name")
    if text_or_name and "text_or_name" not in constraints:
        constraints["text_or_name"] = (
            text_or_name if isinstance(text_or_name, list) else [text_or_name]
        )

    region_hint = target_request.get("region_hint")
    if region_hint and "region_hint" not in constraints:
        constraints["region_hint"] = region_hint

    near = target_request.get("near")
    if near and "near" not in constraints:
        constraints["near"] = near

    return constraints
```

然后 TargetSelector node 使用：

```python
constraints = _normalize_target_constraints(target_request)
```

### 验收

新增测试：

- Planner 输出 `target_request.role="button"`、`text_or_name=["搜索"]`、`constraints={}`。
- TargetSelector 收到的 `target_constraints` 必须包含：
  - `role=["button"]`
  - `text_or_name=["搜索"]`

最低测试方式可以 monkeypatch `TargetSelector.select()` 捕获 query。

## P1：禁止 dp_cli action 失败后回退 Python Coder

### 问题

文件：

- `core/nodes/coder.py`

当前 `_dpcli_action_coder_node()` 在 validation 连续失败后：

```python
return Command(update={
    "execution_mode": "python_code",
    ...
}, goto="Coder")
```

这会重新进入 Python 代码生成链路，违背当前 dp_cli 全面化目标。

### 修改要求

dp_cli 模式下 action 生成失败后的处理顺序应为：

1. 第 1-2 次失败：保留 `execution_mode="dp_cli"`，回到 `Coder` 重试。
2. 超过重试次数：
   - 如果失败原因是缺少目标或 ref：`goto="TargetSelector"` 或 `goto="Planner"`。
   - 如果是 JSON 格式错误：`goto="Planner"` 重新生成结构化计划，或进入 `ErrorHandler`。
   - 不允许设置 `execution_mode="python_code"`。

建议实现：

```python
return Command(
    update={
        "execution_mode": "dp_cli",
        "generated_action": None,
        "_action_source": None,
        "_dpcli_action_disabled": False,
        "error_type": "dpcli_action_json",
        "execution_result": f"dp_cli action generation failed: {validation_error}",
        "reflections": [...],
    },
    goto="Planner",
)
```

如果当前 `dpcli_structured_plan.target_request.required=True` 且 `dpcli_target_result.status != "selected"`，可以路由到 `TargetSelector`。

### 验收

新增测试：

- 构造 dp_cli state，mock LLM 连续输出 invalid action。
- 调用 `_dpcli_action_coder_node()` 到超过重试次数。
- 断言：
  - `execution_mode == "dp_cli"`
  - 不出现 `"python_code"`
  - `goto` 不是为了进入 Python Coder 的路径

## P2：统一 `find` action 的 prompt 与 validator

### 问题

文件：

- `prompts/dpcli_action_prompts.py`
- `core/nodes/_dpcli.py`
- 可能涉及 `skills/dpcli_executor.py`

prompt 允许：

```json
{"skill": "find", "params": {"text": "visible text"}}
```

但 validator 要求 find 必须有 `ref` 或 `locator`：

```python
required = {
    "find": ["ref", "locator"],
}
```

这会导致 prompt 推荐的合法 action 被本地 validator 拒绝。

### 修改要求

明确 `find` 的合法参数：

```python
"find": ["text", "ref", "locator"]
```

同时检查 `skills/dpcli_executor.py` 是否支持 `find text`。如果 executor 不支持，需要在本仓库适配层中补映射，而不是改 prompt 让它说假话。

### 验收

新增测试：

```python
action = {"skill": "find", "params": {"text": "搜索"}}
assert _validate_dpcli_action(action, state) is None
```

并保留：

```python
{"skill": "find", "params": {}}
```

必须返回 validation error。

## P3：前置 dp_cli Planner 分支

### 问题

文件：

- `core/nodes/planner.py`

当前 dp_cli 分支在初始页/已有页面继续判断之后，并且要求：

```python
if is_dpcli and loop_count > 0:
```

这意味着已有页面首轮任务可能先走旧 Planner，再进入 CacheLookup，而不是直接生成 `dpcli_structured_plan`。

### 修改原则

dp_cli structured Planner 应在读取到 `dpcli_agent_view` 后尽早执行。

推荐顺序：

1. 读取 task、loop_count、current_url。
2. 判断是否是空白页/需要 open 起始 URL 的特殊场景。
3. 如果已有 `dpcli_agent_view` 且 `DPCLI_ENABLED=True` 且未显式 `python_code`：
   - 直接调用 `_dpcli_planner_step()`。
4. 只有 dp_cli planner context 缺失或解析失败时，才允许进入 legacy Planner。

### 修改要求

删除或放宽 `loop_count > 0` 条件。更合理的是：

```python
if is_dpcli:
    dpcli_result = _dpcli_planner_step(...)
    if dpcli_result is not None:
        return dpcli_result
```

注意：

- 如果当前是初始空白页且没有 `dpcli_agent_view`，仍可走启动页逻辑。
- 如果 Observer 已经给了 `dpcli_agent_view`，无论 loop_count 是 0 还是 1，都应优先 dp_cli Planner。

### 验收

新增测试：

- state 中有 `dpcli_agent_view`、`DPCLI_ENABLED=True`、`loop_count=0`、`execution_mode=None`。
- mock LLM 返回合法 dp_cli JSON。
- `planner_node()` 应写入 `dpcli_structured_plan`。
- 如果 `target_request.required=True`，应 `goto="TargetSelector"`。
- 不应 `goto="CacheLookup"`。

## P4：修复 smoke 脚本导入方式

### 问题

文件：

- `scripts/smoke_dpcli_snapshot_selector.py`
- `scripts/smoke_dpcli_flow_plan_target.py`

当前 smoke 直接：

```python
from core.nodes.target_selector import TargetSelector
```

Python 会先加载 `core.nodes.__init__`，进而加载 `_utils.py`，当前本地环境缺 `tiktoken` 时 smoke 失败。

### 修改方案

smoke 脚本应避免导入整个 `core.nodes` 包。可选方案：

#### 方案 A：把纯 TargetSelector 类移动到无 LangGraph 依赖模块

推荐新增：

- `skills/dpcli_target_selector.py`

内容：

- `TargetSelector` 纯类
- 不导入 `core.state_v2`
- 不导入 `langgraph`
- 不导入 `langchain`

然后：

- `core/nodes/target_selector.py` 只保留 LangGraph node 包装器，并从 `skills.dpcli_target_selector import TargetSelector`
- smoke 脚本从 `skills.dpcli_target_selector import TargetSelector`

这是最干净的长期方案。

#### 方案 B：脚本内用 importlib 绕过包初始化

不推荐，只能临时使用。因为它会制造维护成本。

### 本轮建议

采用方案 A。

### 验收

以下命令必须在当前环境跑通：

```bash
python scripts/smoke_dpcli_snapshot_selector.py
python scripts/smoke_dpcli_flow_plan_target.py
```

如果仍依赖 `langgraph` 或 `tiktoken`，说明 smoke 没有完成隔离。

## P5：补充 action 生成前置保护

### 问题

即使 prompt 要求 click/type 必须使用 TargetSelector 的 `target_ref`，validator 目前只检查是否有 `ref` 或 `locator`。它没有检查：

- 当前 structured plan 是否需要目标。
- TargetSelector 是否已经 selected。
- LLM 是否绕过 TargetSelector 自己编造 ref。

### 修改要求

在 `_validate_dpcli_action(action, state)` 中增加 dp_cli 目标一致性校验：

1. 如果 `skill in ("click", "type", "select")`：
   - 当前 `dpcli_structured_plan.target_request.required=True` 时，必须有 `dpcli_target_result.status == "selected"`。
   - action 的 `params.ref` 或 `params.target_ref` 必须等于 `dpcli_target_result.target_ref`。
2. 如果不一致：
   - 返回 `"target ref must come from TargetSelector"`。
3. 禁止 click/type 使用 free-form locator。

### 验收

新增测试：

- selected target_ref 为 `e2`，action 使用 `e2`：通过。
- selected target_ref 为 `e2`，action 使用 `e999`：失败。
- target required 但 `dpcli_target_result.status="not_found"`，action click：失败。
- scroll/wait/open 不受此规则影响。

## P6：重新审查 executor 对 target_ref 的兼容

### 检查点

文件：

- `skills/dpcli_executor.py`
- `core/nodes/coder.py`
- `core/nodes/_dpcli.py`

确认 executor 是否把：

```json
{"params": {"target_ref": "e2"}}
```

转换为 dp_cli 能执行的 ref 参数。

如果 executor 只认识 `ref`，则在 action 进入 executor 前 normalize：

```python
if "target_ref" in params and "ref" not in params:
    params["ref"] = params["target_ref"]
```

### 验收

新增测试：

- action `{"skill": "click", "params": {"target_ref": "e2"}}`
- normalize 后 executor 接收到 `ref=e2`

## 推荐执行顺序

### Commit 1：TargetSelector 入参修复

- `_normalize_target_constraints()`
- Planner 一级字段映射到 constraints
- 对应单测

### Commit 2：dp_cli 不再回退 Python

- 修改 `_dpcli_action_coder_node()`
- 超过重试后回 Planner/TargetSelector/ErrorHandler
- 对应单测

### Commit 3：validator 与 prompt 对齐

- `find` 支持 text
- click/type 目标一致性校验
- target_ref normalize
- 对应单测

### Commit 4：Planner 分支前置

- dp_cli planner 优先级调整
- loop_count=0 + agent_view 的测试

### Commit 5：smoke 可运行

- 抽离纯 TargetSelector 到 `skills/dpcli_target_selector.py`
- smoke 改为导入纯模块
- 两个 smoke 脚本跑通

## 最终验收命令

最低必须通过：

```bash
python -m py_compile config.py core/graph_v2.py core/nodes/planner.py core/nodes/coder.py core/nodes/target_selector.py core/nodes/_dpcli.py skills/dpcli_snapshot_indexer.py skills/dpcli_snapshot_query.py skills/dpcli_planner_view.py skills/dpcli_snapshot_store.py
python -m unittest tests.test_target_selector_priority -v
python scripts/smoke_dpcli_snapshot_selector.py
python scripts/smoke_dpcli_flow_plan_target.py
```

如果环境缺 `langgraph`，至少 smoke 脚本不能因为 `langgraph` 或 `tiktoken` 失败。主应用 import 可以继续依赖 requirements，但 smoke 必须证明本轮纯逻辑闭环。

## 完成定义

只有同时满足以下条件，才能说本轮结束：

- Planner 的 `role/text_or_name/region_hint` 能进入 TargetSelector。
- dp_cli action 生成失败不会切到 Python Coder。
- `find {"text": ...}` 在 prompt 和 validator 中一致合法。
- 有 `dpcli_agent_view` 时，首轮 Planner 也优先走 dp_cli structured prompt。
- click/type 的 ref 必须来自 TargetSelector。
- `target_ref` 能被 executor 正常执行或 normalize 为 `ref`。
- 两个 smoke 脚本在当前本地环境跑通。

## 给 opencode 的一句话指令

不要再扩展新架构；请把现有 dp_cli 主链路的 5 个断点修到可运行、可测试、不可回退 Python，并用 smoke 证明 Plan -> Target -> Action 闭环。
