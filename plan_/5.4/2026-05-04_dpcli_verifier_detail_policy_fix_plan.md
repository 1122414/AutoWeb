# 2026-05-04 dp_cli Verifier 详情批处理策略绕过修正计划

## 背景

上一轮改造为 dp_cli Verifier 增加了确定性验收分支：

- `snapshot` / `expand` / `find` 等 observation action：只验收 `ok=true`，不要求 URL/DOM 变化。
- `extract` / `list-items` / `batch-detail-extract` 等 data action：验收是否返回可用 items。
- page action：继续交给 LLM Verifier。

这个方向是对的，但当前实现引入了一个新的控制流问题。

在 [core/nodes/verifier.py](../../core/nodes/verifier.py) 中，确定性验收成功后会直接：

```python
return Command(update=updates, goto="Observer")
```

这导致原本位于 LLM success 分支后面的详情批处理策略不会执行：

```python
from skills.dpcli_crawl_policy import (
    build_detail_batch_action,
    should_run_detail_batch,
)

if should_run_detail_batch(policy_state):
    detail_action = build_detail_batch_action(policy_state)
    ...
    return Command(update=updates, goto="Executor")
```

因此对于用户任务：

> 获取榜单小说信息，并点击各个小说获取简介

`extract` 成功拿到榜单列表后，Verifier 会认为 data action 成功并返回 Observer，但不会调度 `batch-detail-extract`，导致“获取简介/详情”这一步断掉。

## 问题本质

Verifier 中存在两条 success 通道：

1. dp_cli deterministic success
2. LLM verifier success

但详情批处理策略只挂在第 2 条通道上。

这违反了当前链路的真实语义：只要 dp_cli `extract` 成功返回带 URL 的 items，且用户任务需要详情，就应该触发 `batch-detail-extract`，不应关心这个 success 是确定性 Verifier 给出的，还是 LLM Verifier 给出的。

## 修正目标

1. `extract` data action 经过确定性验收成功后，仍然运行 `should_run_detail_batch()`。
2. observation action 成功后仍然只回 Observer，不触发详情策略。
3. data action 成功后的 ActionCache、失败缓存清理、detail policy 等后处理逻辑只保留一份，避免 Verifier 两套 success 分支继续分叉。
4. 增加真实 Verifier 控制流测试，覆盖：
   - `extract` success + detail task + items with url -> goto Executor，生成 `batch-detail-extract`。
   - `extract` success + non-detail task -> goto Observer。
   - `snapshot` observation success -> goto Observer，且不生成 detail action。

## 推荐实现

### P0：抽取 dp_cli success 后处理函数

在 [core/nodes/verifier.py](../../core/nodes/verifier.py) 中抽取一个 helper：

```python
def _handle_dpcli_success_after_verification(
    state: AgentState,
    updates: dict,
    task: str,
    current_plan: str,
    current_url: str,
    summary: str,
) -> Optional[Command]:
    ...
```

职责：

1. 保存 ActionCache。
2. 执行 detail batch policy。
3. 如果触发详情批处理，返回 `Command(..., goto="Executor")`。
4. 如果未触发，返回 `None`，让调用方继续原有路由。

伪代码：

```python
def _handle_dpcli_success_after_verification(...):
    try:
        from config import ACTION_CACHE_ENABLED
        if ACTION_CACHE_ENABLED and state.get("_action_source") != "action_cache":
            from skills.action_cache import action_cache_manager
            action_cache_manager.save(
                user_task=task,
                goal=current_plan,
                url=current_url,
                action=state.get("generated_action") or {},
                snapshot_view=state.get("dpcli_snapshot_view"),
                result_summary=summary,
            )
    except Exception as action_store_exc:
        logger.info(...)

    try:
        from skills.dpcli_crawl_policy import (
            build_detail_batch_action,
            should_run_detail_batch,
        )
        policy_state = dict(state)
        policy_state.update(updates)
        if should_run_detail_batch(policy_state):
            detail_action = build_detail_batch_action(policy_state)
            item_count = len(detail_action.get("params", {}).get("items", []))
            logger.info(
                f"   dp_cli extract OK + detail task({item_count}) -> batch-detail-extract"
            )
            updates.update({
                "generated_action": detail_action,
                "generated_code": None,
                "execution_mode": "dp_cli",
                "dpcli_detail_batch_ran": True,
                "_action_source": "policy",
            })
            return Command(update=updates, goto="Executor")
    except Exception as policy_exc:
        logger.info(...)

    return None
```

### P1：deterministic success 分支调用公共后处理

当前确定性分支大致是：

```python
if is_success:
    if _dpcli_action_kind(state.get("generated_action") or {}) != "observation":
        updates["finished_steps"] = [summary]
    updates["_failed_code_cache_ids"] = []
    updates["_failed_dom_cache_ids"] = []
    updates["_cache_hit_id"] = None
    return Command(update=updates, goto="Observer")
```

改为：

```python
if is_success:
    action_kind = _dpcli_action_kind(state.get("generated_action") or {})
    if action_kind != "observation":
        updates["finished_steps"] = [summary]
    updates["_failed_code_cache_ids"] = []
    updates["_failed_dom_cache_ids"] = []
    updates["_cache_hit_id"] = None

    if action_kind == "data":
        detail_cmd = _handle_dpcli_success_after_verification(
            state=state,
            updates=updates,
            task=task,
            current_plan=current_plan,
            current_url=current_url,
            summary=summary,
        )
        if detail_cmd is not None:
            return detail_cmd

    return Command(update=updates, goto="Observer")
```

注意：

- 只对 `action_kind == "data"` 跑 detail policy。
- observation action 不跑 detail policy，避免 snapshot/expand 成功后误触发。
- 如果未来需要 ActionCache 覆盖 observation，也可以调整，但当前目标是修详情链路，先保持最小改动。

### P2：LLM success 分支复用同一个 helper

当前 LLM success 分支里有一大段重复逻辑：

- ActionCache save
- should_run_detail_batch
- build_detail_batch_action
- return Executor

建议删除这段内联逻辑，改为：

```python
if state.get("execution_mode") == "dp_cli":
    detail_cmd = _handle_dpcli_success_after_verification(
        state=state,
        updates=updates,
        task=task,
        current_plan=current_plan,
        current_url=current_url,
        summary=summary,
    )
    if detail_cmd is not None:
        return detail_cmd
```

这样两条 success 通道共享同一个后处理，不会再出现 deterministic 分支漏掉策略的问题。

## 测试计划

新增或扩展测试文件：

- `tests/test_dpcli_verifier_detail_policy.py`

### 测试 1：deterministic extract success 触发 batch-detail-extract

构造 state：

```python
state = {
    "execution_mode": "dp_cli",
    "user_task": "获取榜单小说信息，并点击各个小说获取简介",
    "plan": '{"step_intent":"extract"}',
    "generated_action": {"skill": "extract", "params": {"target_ref": "r1"}},
    "dpcli_result": {
        "ok": True,
        "action": "extract",
        "data": {
            "page": {"url": "https://www.qidian.com/rank/"},
            "items": [
                {"title": "Book A", "url": "https://book-a"},
                {"title": "Book B", "detail_url": "https://book-b"},
            ],
        },
    },
    "dpcli_detail_batch_ran": False,
}
```

预期：

- `verifier_node(...)` 不调用 LLM。
- `goto == "Executor"`。
- `update["generated_action"]["skill"] == "batch-detail-extract"`。
- `update["dpcli_detail_batch_ran"] is True`。
- `generated_action.params.items` 包含两个带 URL 的 item。

### 测试 2：deterministic extract success 非详情任务不触发 batch

任务：

```python
"只获取榜单小说标题"
```

预期：

- `goto == "Observer"`。
- `generated_action` 不被替换为 `batch-detail-extract`。
- `finished_steps` 包含 data action success summary。

### 测试 3：snapshot observation success 不触发 batch

构造：

```python
generated_action = {"skill": "snapshot", "params": {"mode": "agent_summary"}}
dpcli_result = {"ok": True, "action": "snapshot", "data": {"page": {...}, "index": {...}}}
```

预期：

- `goto == "Observer"`。
- 不写用户意义上的 `finished_steps`，或保持当前约定。
- 不生成 `batch-detail-extract`。

### 测试 4：LLM success 分支仍触发 batch

构造 page action 或未被 deterministic 覆盖的 dp_cli success，让 LLM 返回 `STEP_SUCCESS`，同时 state 中有 `extract` result + detail task。

预期：

- 仍然触发 `batch-detail-extract`。
- 证明抽取 helper 没有破坏原分支。

## 兼容性注意

### 中文任务关键词

`skills/dpcli_crawl_policy.py` 中的 `DETAIL_GOAL_TOKENS` 当前有编码异常风险。测试中如果中文 token 匹配不稳定，可以至少用英文 `detail/description` 路径补充一个稳定测试。

长期建议另开计划修复中文 token 编码：

- `简介`
- `详情`
- `介绍`
- `description`
- `detail`

### ActionCache 不应阻断详情策略

ActionCache 保存失败只能记录日志，不应影响 detail policy。

### batch-detail-extract 自身是 data action

当 `batch-detail-extract` 执行成功后，Verifier 确定性 data 分支可能会再次进入 success 后处理。必须依赖：

```python
if state.get("dpcli_detail_batch_ran"):
    return False
```

避免递归调度。

## 验收命令

```bash
python -m py_compile core/nodes/verifier.py skills/dpcli_crawl_policy.py
python -m unittest tests.test_dpcli_verifier_detail_policy
python -m unittest discover -s tests -p "test_dpcli*.py"
```

如果环境缺少完整依赖，可先将后处理逻辑拆成纯函数并对纯函数做单元测试。

## 完成标准

修复后，同一条任务应形成如下链路：

```text
extract 榜单列表
Verifier deterministic data success
should_run_detail_batch == True
generated_action = batch-detail-extract
goto Executor
Executor 执行详情批处理
Verifier 验收详情数据
Planner 决定是否 finish
```

不应再出现：

```text
extract success -> Verifier deterministic success -> Observer
```

导致详情简介永远不执行。

