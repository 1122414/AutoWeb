# 2026-05-04 dp_cli 内部观察动作与 Verifier 误判修正计划

## 背景

当前任务：

> 去起点中文网排行榜获取各榜单小说信息，并点击各个小说获取简介。

运行中出现新的循环：

1. Planner 看到 `dpcli_agent_view.coverage.omitted_groups` 中有大量压缩组。
2. Planner 生成 `step_intent=expand`，语义是“展开本地 snapshot 压缩组，以便看见更多结构”。
3. TargetSelector 找不到页面上的 `generic/page_body` 目标，Coder 退回生成 `snapshot`。
4. Executor 成功执行 `snapshot`。
5. Verifier 按“页面动作”验收，要求 URL、DOM 或页面可见内容发生变化。
6. 由于 snapshot/压缩组展开本来就不是页面交互，Verifier 判 FAIL。
7. Observer 重新保存 snapshot，Planner 再次生成“展开所有压缩组”，形成循环。

这不是浏览器自动化失败，而是 `dp_cli` 三层感知架构的动作契约没有分清：

- 页面动作：`open`、`click`、`type`、`scroll`、`navigate` 等，会影响页面状态或浏览器位置。
- 数据动作：`extract`、`list-items`、`batch-detail-extract` 等，目标是产出结构化数据。
- 内部观察动作：`snapshot`、`expand`、`resolve-locator` 等，目标是改善 agent 可见上下文，不要求页面变化。

Verifier 当前把内部观察动作当成页面动作验收，所以它在逻辑上必然误判。

## 修正目标

让 Verifier 只验收“本轮计划真正承诺的动作效果”，而不是用 URL/页面变化套所有 dp_cli action。

具体目标：

1. `snapshot` 成功时，Verifier 不要求 URL 或 DOM 变化。
2. 本地压缩组 `expand` 成功时，Verifier 验收“返回了 expanded nodes/items 或更新了可用观察上下文”，不要求页面变化。
3. Planner 不再生成“展开所有压缩页面元素组”这类不可执行的大动作。
4. 如果 Planner 想看更多内容，应生成有限、可执行的内部观察计划，例如“展开候选 data_region/top_level_group 中最相关的一个”。
5. 对榜单采集任务，Planner 应优先使用 `extract` / `list-items`，而不是反复 snapshot。

## 核心判断

“展开所有压缩页面元素组”不是用户目标，也不是页面动作。

它只是 agent 的内部观察诉求。Verifier 不应把它写进用户任务进度，也不应要求页面变化。成功的内部观察动作最多说明：

- 当前 snapshot 已获取；
- 某个 ref/group 已被本地展开；
- planner 可获得更多候选区域；
- 下一步应该继续规划 `extract`、`list-items`、`click` 或 `batch-detail-extract`。

## P0：引入 dp_cli action 分类

建议新增一个纯函数，位置可放在 `core/nodes/_dpcli.py`：

```python
def _dpcli_action_kind(action: dict) -> str:
    skill = str((action or {}).get("skill") or "").strip().lower()
    if skill in {"snapshot", "expand", "resolve-locator", "find", "session.inspect", "session_inspect"}:
        return "observation"
    if skill in {"extract", "list-items", "batch-detail-extract"}:
        return "data"
    if skill in {"open", "navigate", "click", "type", "scroll", "wait"}:
        return "page"
    return "unknown"
```

注意：

- `find` 默认归为 observation，因为它通常只定位候选元素，不改变页面。
- `wait` 可以保留为 page，但 Verifier 对它也不应强制要求 URL 变化。
- `expand` 在当前项目中必须明确为 observation，不是页面上的展开按钮点击。

## P1：Verifier 增加 dp_cli 确定性验收分支

位置：[core/nodes/verifier.py](../../core/nodes/verifier.py)

在调用 `VERIFIER_CHECK_PROMPT` 之前，先处理 dp_cli observation/data action 的确定性结果。

伪代码：

```python
def _verify_dpcli_action_deterministically(state):
    action = state.get("generated_action") or {}
    result = state.get("dpcli_result") or {}
    kind = _dpcli_action_kind(action)
    skill = str(action.get("skill") or "").lower()

    if not result.get("ok"):
        return None  # 保持现有失败流程

    if kind == "observation":
        return _build_verification_result(
            is_success=True,
            is_done=False,
            summary=f"dp_cli observation action succeeded: {skill}",
            source="verifier",
            failure_scope="local",
            evidence=json.dumps(_compact_result_evidence(result), ensure_ascii=False),
            fix_hint="continue planning with the updated snapshot context",
        )

    if kind == "data":
        if _result_has_items_or_data(result):
            return _build_verification_result(
                is_success=True,
                is_done=False,
                summary=f"dp_cli data action succeeded: {skill}",
                source="verifier",
                evidence=json.dumps(_compact_result_evidence(result), ensure_ascii=False),
            )
        return _build_verification_result(
            is_success=False,
            is_done=False,
            summary=f"dp_cli data action returned no usable items: {skill}",
            source="verifier",
            failure_scope="local",
            fix_hint="select a better data region or list ref",
        )

    return None
```

路由规则：

- 如果 deterministic result 是 success：
  - 写入 `verification_result`。
  - observation action 不写入用户意义上的 `finished_steps`，或只写入一条内部步骤标记，例如 `[internal] snapshot refreshed`。
  - 返回 `Observer` 或 `Planner` 都可以，但建议返回 `Observer`，保持当前主循环一致。
- 如果 deterministic result 是 fail：
  - 按现有失败逻辑走 `Observer`，但 `fix_hint` 要指向“选更好的 data region/ref”，不是要求页面变化。
- 如果 action kind 是 `page` 或 `unknown`：
  - 继续走现有 LLM Verifier。

## P2：Verifier Prompt 加入动作类型与禁止项

位置：[prompts/verifier_prompts.py](../../prompts/verifier_prompts.py)

给 `VERIFIER_CHECK_PROMPT` 增加字段：

- `generated_action`
- `dpcli_action_kind`
- `dpcli_result_summary`
- `structured_plan`

并写入规则：

```text
If dpcli_action_kind is observation:
- Verify only whether the observation command succeeded.
- Do not require URL changes.
- Do not require visible DOM changes.
- Do not treat "expanded compressed snapshot groups" as a browser interaction.
- The step is successful if the command returned ok=true and provided snapshot/index/expanded data.

If dpcli_action_kind is data:
- Verify whether usable data/items were returned.
- Do not require URL changes.

If dpcli_action_kind is page:
- Verify page/action effects relevant to the plan.
```

这能兜住未被确定性分支覆盖的情况，避免 LLM 再用“页面没变化”误判 observation。

## P3：Planner 禁止“展开所有压缩组”

位置：[prompts/dpcli_planner_prompts.py](../../prompts/dpcli_planner_prompts.py)

Planner prompt 需要明确：

1. 压缩组是 agent 视图压缩，不等于页面折叠 UI。
2. 不要计划“展开所有压缩组”。
3. 如果需要更多上下文，只能选择一个最相关的 `data_region`、`top_level_group` 或 `content_region` 展开。
4. 对采集类任务，优先计划 `extract` 或 `list-items`。
5. 只有当页面信息完全不足且没有候选 data region 时，才允许 `snapshot`。

建议新增规则：

```text
Compressed groups are an internal snapshot representation, not visible page controls.
Never ask to expand all compressed groups.
For data collection tasks, prefer extract/list-items on the most relevant data region.
Use expand only for one specific region/group when it will reveal fields needed for the next extraction.
An observation step is not user-visible progress; after it succeeds, continue to data extraction.
```

## P4：TargetSelector 支持 observation 的特殊目标

当前 Planner 对 `expand` 生成：

```json
{
  "target_request": {
    "required": true,
    "role": "generic",
    "region_hint": "page_body"
  }
}
```

这会让 TargetSelector 在普通 element/container 里查 `generic/page_body`，自然找不到。

修正方向：

1. `step_intent=expand` 不应使用 `role=generic`。
2. Planner 若要 expand，应指向：
   - `target_request.role = "region"` 或 `"group"`；
   - `region_hint = "data_regions"` / `"top_level_groups"` / `"content_regions"`；
   - `target_hint` 是“排行榜列表/小说列表/榜单区域”，不是“展开所有压缩组”。
3. TargetSelector 对 `intent=expand` 应优先从 `dpcli_agent_view.capability_map.data_regions`、`top_level_groups`、`compressed_index.groups` 选择候选，而不是只查 `by_ref` 中的普通 element。

可选实现：

- 在 `TargetSelector._retrieve_candidates()` 中，如果 `intent in ("expand", "list-items", "extract")`：
  - 不强制 `ref_type="element"`；
  - 允许 `ref_type="container"`；
  - 允许返回 `region.ref` 或 `group_id`。

## P5：Coder 对 observation failure 的策略

位置：[prompts/dpcli_action_prompts.py](../../prompts/dpcli_action_prompts.py)

当 `dpcli_target_result.status == "not_found"` 且 `step_intent == "expand"` 时，现在 Coder 倾向生成 `snapshot`，导致重复。

应改为：

1. 如果 expand 找不到目标，不要继续 snapshot 超过一次。
2. 优先退到 `extract` / `list-items` 的候选 data region。
3. 如果没有候选 data region，返回 `find` 或 `snapshot(mode="full")`，但 Verifier 只能把它当 observation success。

建议 prompt 规则：

```text
If an expand target is not found, do not loop snapshot.
Use extract/list-items when the planner goal is data collection and data_regions are available.
Snapshot is only a context refresh, not progress toward data extraction.
```

## P6：Executor 日志标注 internal observation

位置：[core/nodes/coder.py](../../core/nodes/coder.py) 或 [core/nodes/executor.py](../../core/nodes/executor.py)

为了让 Verifier 和日志更清楚，dp_cli 执行报告里应包含：

```json
{
  "action_kind": "observation|data|page",
  "page_effect_expected": false,
  "verification_contract": "ok=true and snapshot/index data returned"
}
```

对于 `snapshot` / `expand`：

- `page_effect_expected=false`
- `url_change_expected=false`
- `dom_change_expected=false`

这样即使走 LLM Verifier，也不会被“URL 未变化”带偏。

## P7：状态字段建议

可以在 `AgentState` 中增加两个可选字段：

```python
dpcli_action_kind: Optional[str]
dpcli_verification_contract: Optional[Dict[str, Any]]
```

也可以不扩 state，直接从 `generated_action` 动态计算。短期建议动态计算，减少 schema 改动。

## 测试计划

新增测试文件建议：

- `tests/test_dpcli_verifier_action_contract.py`
- `tests/test_dpcli_planner_no_expand_all.py`

### 测试 1：snapshot 不要求页面变化

构造 state：

```python
generated_action = {"skill": "snapshot", "params": {"mode": "agent_summary"}}
dpcli_result = {"ok": True, "action": "snapshot", "data": {"page": {"url": "https://www.qidian.com/rank/"}, "index": {"stats": {"total_nodes": 1110}}}}
plan = '{"step_intent":"snapshot"}'
```

预期：

- Verifier 返回 success。
- summary 表示 observation succeeded。
- 不要求 URL 变化。

### 测试 2：expand 是内部观察动作

构造 state：

```python
generated_action = {"skill": "expand", "params": {"ref": "r10", "depth": 2}}
dpcli_result = {"ok": True, "action": "expand", "data": {"items": [{"ref": "e1"}]}}
```

预期：

- Verifier success。
- 不检查 URL 是否变化。
- 不要求页面 DOM 可见变化。

### 测试 3：extract 无数据才失败

构造：

```python
generated_action = {"skill": "extract", "params": {"target_ref": "r10"}}
dpcli_result = {"ok": True, "action": "extract", "data": {"items": []}}
```

预期：

- Verifier fail。
- failure_scope=local。
- fix_hint 指向重新选择 data region/list ref。

### 测试 4：Planner 不生成“展开所有压缩组”

mock `dpcli_agent_view.coverage.omitted_groups` 很大，但同时有 data region。

预期 Planner JSON：

- 不包含 `target_hint="展开所有压缩..."`。
- 不包含 “all compressed groups” / “所有压缩组”。
- 优先 `step_intent in {"extract", "list-items"}`。

## 实施顺序

1. 在 `_dpcli.py` 增加 action kind / verification contract helper。
2. 在 Verifier 中加入 dp_cli observation/data 确定性验收分支。
3. 更新 Verifier prompt，禁止 observation 按 URL/DOM 变化验收。
4. 更新 dp_cli Planner prompt，禁止“展开所有压缩组”。
5. 更新 TargetSelector 对 `expand/list-items/extract` 的 region/container/group 候选支持。
6. 更新 Coder prompt，避免 expand not_found 后无限 snapshot。
7. 补测试。

## 验收命令

```bash
python -m py_compile core/nodes/verifier.py core/nodes/_dpcli.py core/nodes/planner.py core/nodes/target_selector.py core/nodes/coder.py
python -m unittest tests.test_dpcli_verifier_action_contract
python -m unittest discover -s tests -p "test_dpcli*.py"
python scripts/smoke_dpcli_snapshot_selector.py
```

如果当前环境缺 `tiktoken`，则先跑不依赖完整 `core.nodes` import 链的纯函数测试，并记录依赖缺失。

## 成功标准

同一个起点排行榜任务不应再出现：

```text
计划要求展开所有压缩的页面元素组，但执行日志仅展示页面 DOM 结构统计，URL 未变化，因此失败。
```

取而代之的期望链路：

```text
Observer 保存 snapshot/index/planner_view
Planner 选择排行榜 data_region
TargetSelector 选择 region/container/group ref
Coder 生成 extract 或 list-items
Executor 返回榜单小说条目
Verifier 验收数据动作成功
Planner 继续详情页 batch-detail-extract 或逐条 click/extract
```

核心原则：

> Verifier 验收 action contract，不验收自己想象中的页面变化。

