# AutoWeb dp_cli 全面升级计划

生成日期：2026-05-03

## 1. 背景与目标

当前 AutoWeb 已接入 `dp_cli`，但系统主链路仍然保留旧的“Observer 生成定位建议，Coder 生成 Python 代码，Executor 执行 Python”的架构惯性。结果是底层执行能力切到了 `dp_cli`，但上层感知、规划、动作生成、验证和缓存没有同步切换，导致系统出现割裂，实际能力反而下降。

本计划目标是完成一次完整迁移：

- 不再由 LLM 生成浏览器自动化 Python 代码。
- 不再通过 `BrowserActor.execute_python_strategy()` 执行网页操作。
- 所有网页操作统一表达为 `dp_cli` 结构化 action。
- Observer、Planner、ActionBuilder、Executor、Verifier 全面围绕 `dp_cli snapshot / ref / result` 重新设计。
- 不确定或高风险动作进入人工审批。

说明：这里的“去 Python 代码化”指去掉 LLM 生成并执行网页自动化 Python 脚本。AutoWeb 项目本身仍然是 Python 工程。

## 2. 当前系统割裂点

### 2.1 Observer 割裂

当前 dp_cli 路径下，Observer 只调用：

```text
python -m dp_cli snapshot --mode agent_summary
```

然后写入：

- `dpcli_snapshot`
- `dpcli_snapshot_view`
- `dom_skeleton`
- `current_url`

但它不再调用旧 Observer 的 `analyze_locator_strategy()`，因此不会产出高质量的 `locator_suggestions`。

旧链路是：

```text
DOM -> LLM 预分析目标元素 -> locator_suggestions -> Coder 写 Python
```

当前 dp_cli 链路变成：

```text
snapshot -> 简单投影 -> Planner/Coder 自己猜下一步
```

### 2.2 Planner 割裂

Planner 仍然围绕旧字段 `locator_suggestions` 组织 prompt。dp_cli 的 `interactable_elements`、`data_regions`、`surface_index` 没有成为 Planner 的核心输入。

这导致 Planner 看不到真正的 ref、数据区域、页面语义结构，只能基于 URL、历史步骤和少量文本做推断。

### 2.3 Coder 割裂

dp_cli action prompt 要求：

- 优先使用 snapshot refs。
- click/type 使用 `e*` ref。
- extract/list-items/expand 使用 `r*` ref。
- 不要虚构 ref。

但 `_dpcli_action_context()` 当前没有把 `dpcli_snapshot_view` 中的 refs 传给 Coder。Coder 被要求使用 ref，却拿不到 ref，这是当前能力下降的核心问题。

### 2.4 Executor 割裂

Executor 同时保留两套模式：

- `python_code`：执行 LLM 生成代码。
- `dp_cli`：执行结构化 action。

这导致错误处理、缓存、验证、人工确认都需要同时兼容两套范式，系统复杂度上升，行为不稳定。

### 2.5 Verifier 割裂

Verifier 仍然以自然语言 execution log 为主要依据，未充分消费 `dp_cli` 的结构化 result，例如：

- action 类型
- page identity
- snapshot id
- item count
- target state
- error code

因此 dp_cli 的可验证性优势没有发挥出来。

## 3. 目标架构

目标链路：

```text
DPObserver
  -> DPPlanner
  -> ActionCacheLookup
  -> TargetSelector
  -> ActionBuilder
  -> ApprovalGate
  -> DPExecutor
  -> DPVerifier
  -> DPObserver / DPPlanner / RAG / END
```

核心原则：

- Snapshot 是唯一页面感知来源。
- Ref 是默认交互句柄。
- Action 是唯一执行单位。
- Result 是验证和恢复的主要依据。
- LLM 负责理解和选择，不负责写执行代码。

## 4. 新状态模型

建议新增或重命名以下 state 字段：

```python
dpcli_snapshot: dict | None
dpcli_snapshot_view: dict | None
dpcli_view_text: str | None
dpcli_page_identity: dict | None
dpcli_last_result: dict | None

structured_plan: dict | None
target_selection: dict | None
generated_action: dict | None
action_risk: dict | None
approval_request: dict | None
dpcli_verification: dict | None
```

逐步废弃：

```python
generated_code
execution_mode == "python_code"
locator_suggestions  # dp_cli-only 模式下废弃
_code_source
_cache_hit_id
_failed_code_cache_ids
```

## 5. 阶段一：冻结旧链路

### 5.1 新增配置

新增：

```python
DPCLI_ONLY_MODE = _env_bool("DPCLI_ONLY_MODE", "False")
DPCLI_ALLOW_PYTHON_FALLBACK = _env_bool("DPCLI_ALLOW_PYTHON_FALLBACK", "False")
DPCLI_APPROVAL_CONFIDENCE_THRESHOLD = float(os.getenv("DPCLI_APPROVAL_CONFIDENCE_THRESHOLD", "0.72"))
DPCLI_MAX_SNAPSHOT_INTERACTABLES = int(os.getenv("DPCLI_MAX_SNAPSHOT_INTERACTABLES", "50"))
DPCLI_MAX_SNAPSHOT_REGIONS = int(os.getenv("DPCLI_MAX_SNAPSHOT_REGIONS", "10"))
DPCLI_ALLOW_FREEFORM_LOCATOR = _env_bool("DPCLI_ALLOW_FREEFORM_LOCATOR", "False")
DPCLI_ALLOW_EVAL = _env_bool("DPCLI_ALLOW_EVAL", "False")
```

### 5.2 行为要求

当 `DPCLI_ONLY_MODE=True`：

- `execution_mode` 必须固定为 `dp_cli`。
- Coder 不允许生成 Python。
- Executor 不允许调用 `BrowserActor.execute_python_strategy()`。
- CodeCache 不允许直接命中并执行 Python 代码。
- `generated_code` 必须始终为空。

### 5.3 验收标准

- 单元测试覆盖 `DPCLI_ONLY_MODE=True`。
- 任意任务执行中不会进入 `python_code`。
- HITL 编辑时编辑的是 action JSON，不是 Python 代码。

## 6. 阶段二：重做 DPObserver

### 6.1 新输出契约

DPObserver 输出：

```json
{
  "source": "dp_cli_snapshot",
  "page": {},
  "page_identity": {},
  "interactable_elements": [],
  "data_regions": [],
  "surface_index": [],
  "stats": {},
  "last_result": {},
  "available_actions": []
}
```

### 6.2 裁剪策略

默认：

- `interactable_elements`: 前 50 个
- `data_regions`: 前 10 个
- `surface_index`: 前 80 个

裁剪时优先保留：

- button
- link
- input
- textarea
- select
- checkbox
- pagination
- search
- list/data region
- dialog controls

### 6.3 不再伪装为 DOM

当前把 dp_cli view 塞进 `dom_skeleton`，容易误导旧 prompt。建议新增 `dpcli_view_text`，Planner 和 ActionBuilder 明确读取该字段。

### 6.4 不确定点审批

需要用户审批：

- snapshot 传给 LLM 的最大元素数量。
- snapshot 失败时是否允许 fallback 到旧 DOM Observer。
- 是否保留旧 `dom_skeleton` 字段作为兼容层。

默认建议：

- 元素数量默认 50。
- dp_cli-only 下不 fallback 到旧 DOM。
- 短期保留 `dom_skeleton`，但 prompt 不再依赖它。

## 7. 阶段三：重做 DPPlanner

### 7.1 Planner 输入

DPPlanner 必须读取：

- user task
- current URL
- finished steps
- last dp_cli result
- current snapshot view
- interactable elements
- data regions
- previous verification
- failure focus

### 7.2 Planner 输出

Planner 不再只输出自然语言计划，应输出结构化计划：

```json
{
  "status": "continue",
  "intent": "click",
  "goal": "点击搜索按钮进入结果页",
  "target_hint": "搜索按钮",
  "target_type": "element",
  "expected_evidence": "URL 进入搜索结果页，或页面标题/列表发生变化",
  "risk": "low"
}
```

可选 `status`：

- `continue`
- `done`
- `need_user`
- `blocked`

可选 `intent`：

- `open`
- `snapshot`
- `find`
- `click`
- `type`
- `expand`
- `list-items`
- `extract`
- `batch-detail-extract`
- `rag_store`
- `ask_user`

### 7.3 单步原则

第一阶段仍保持每轮一个 action。复杂任务通过循环完成。

例外：

- `batch-detail-extract`
- `extract` 内部可以批量处理同一区域。

### 7.4 不确定点审批

需要用户审批：

- Planner 是否允许输出多 action plan。
- 搜索操作是否拆成 `type` + `click`，还是扩展 dp_cli 支持 `press-enter`。
- 跨域导航是否进入 ApprovalGate。

默认建议：

- 初期只允许单 action。
- 搜索拆成 `type` + `click`，后续补 `press-enter`。
- 跨域默认审批。

## 8. 阶段四：新增 TargetSelector

### 8.1 职责

TargetSelector 负责从 snapshot 中选择目标 ref。它是当前系统最缺的一层。

输入：

```json
{
  "structured_plan": {},
  "dpcli_snapshot_view": {},
  "last_failure": {}
}
```

输出：

```json
{
  "target_ref": "e12",
  "target_kind": "element",
  "skill": "click",
  "confidence": 0.86,
  "reason": "该元素 role=button 且 name=搜索，符合当前计划",
  "alternatives": [
    {"ref": "e13", "reason": "另一个搜索入口"}
  ]
}
```

### 8.2 强约束

- `click` / `type` 必须使用 `e*`。
- `extract` / `expand` / `list-items` 必须使用 `r*`。
- 如果 snapshot 中存在 ref，默认禁止 free-form locator。
- 低置信度进入 ApprovalGate。
- 找不到目标时返回 `need_snapshot` 或 `need_user`，不能猜。

### 8.3 低置信度规则

触发审批：

- `confidence < 0.72`
- top1 与 top2 差距小于 0.1
- 目标 action 是提交、删除、发布、支付、登录
- action 需要 free-form locator
- 目标 ref 类型与 action 不匹配

### 8.4 不确定点审批

需要用户审批：

- 低置信度阈值。
- 多候选目标是否自动选择 top1。
- 是否允许 free-form locator。

默认建议：

- 阈值 0.72。
- top1/top2 接近时审批。
- dp_cli-only 下默认禁止 free-form locator。

## 9. 阶段五：Coder 改造为 ActionBuilder

### 9.1 职责变化

旧 Coder：

```text
plan + locator_suggestions -> Python code
```

新 ActionBuilder：

```text
structured_plan + target_selection -> dp_cli action JSON
```

### 9.2 输出格式

只允许输出：

```json
{
  "skill": "click",
  "params": {
    "ref": "e12"
  },
  "reason": "点击搜索按钮"
}
```

### 9.3 禁止项

dp_cli-only 模式下禁止：

- `generated_code`
- `python_code`
- `eval`
- 任意 JS
- 任意文件系统操作
- 任意 shell 操作

### 9.4 校验规则

ActionBuilder 后必须执行 action validation：

- skill 在白名单中。
- 参数完整。
- ref 类型匹配。
- ref 存在于当前 snapshot。
- free-form locator 符合配置。
- action 风险等级已计算。

### 9.5 不确定点审批

需要用户审批：

- `eval` 是否彻底禁用。
- `resolve-locator` 是否保留。
- `wait_time` 最大值。

默认建议：

- `eval` 默认禁用。
- `resolve-locator` 保留为诊断动作。
- `wait_time` 最大 10 秒。

## 10. 阶段六：Executor 全面 dp_cli 化

### 10.1 Executor 输入

Executor 只接收：

```python
generated_action: dict
```

### 10.2 删除旧分支

dp_cli-only 模式下不允许：

- `scan_code_safety(code)`
- `BrowserActor(tab, browser)`
- `actor.execute_python_strategy(...)`
- `_handle_cache_failure()` 处理 CodeCache 命中代码

### 10.3 失败路由

建议路由：

```text
ref_stale -> DPObserver
invalid_action -> ActionBuilder
invalid_ref_type -> TargetSelector
target_not_found -> TargetSelector
process_error -> ErrorHandler
timeout -> DPObserver 或 ApprovalGate
snapshot_failed -> ErrorHandler
```

### 10.4 执行结果统一写入

```python
dpcli_result
dpcli_last_result
current_url
dpcli_page_identity
execution_log
```

### 10.5 不确定点审批

需要用户审批：

- dp_cli 进程错误时是否自动重启 session。
- timeout 是否自动重试。
- 高风险 action 是否总是人工确认。

默认建议：

- 进程错误不自动重启，先报错审批。
- timeout 最多自动重试 1 次。
- 高风险 action 必须人工确认。

## 11. 阶段七：DPVerifier 重做

### 11.1 验证原则

优先确定性验证，必要时再 LLM 验证。

### 11.2 确定性规则

`open`：

- URL 变化。
- title 可用。
- snapshot 可用。

`click`：

- URL 变化，或
- page identity / snapshot seq 变化，或
- 目标状态变化，或
- 出现预期元素。

`type`：

- target value 包含输入文本。
- 或下一次 snapshot 中对应输入控件 value 更新。

`extract`：

- items 数量大于 0。
- schema 字段覆盖率达标。
- 输出数据非空。

`batch-detail-extract`：

- 输出文件存在。
- detail count 大于 0。
- 失败率低于阈值。

`snapshot`：

- index 存在。
- refs 可用。
- page identity 可用。

### 11.3 Verifier 输出

```json
{
  "is_success": true,
  "is_done": false,
  "summary": "已点击搜索按钮，页面进入搜索结果页",
  "evidence": {},
  "failure_scope": "local",
  "next_hint": "继续提取结果列表"
}
```

### 11.4 不确定点审批

需要用户审批：

- `extract` 几条数据算成功。
- 批量详情部分失败是否继续。
- 是否允许覆盖输出文件。

默认建议：

- 至少 1 条且字段覆盖率超过 50%。
- 部分失败率低于 20% 可继续。
- 文件覆盖必须审批。

## 12. 阶段八：缓存体系迁移

### 12.1 旧缓存状态

旧缓存：

- DomCache
- CodeCache
- ActionCache

dp_cli-only 后：

- CodeCache 不再直接执行。
- DomCache 不再作为主定位来源。
- ActionCache 成为主要缓存。

### 12.2 新缓存建议

新缓存：

```text
SnapshotPatternCache
ActionCache
VerificationCache
```

ActionCache 不应缓存短期 ref，例如 `e12`。它应缓存目标选择规则：

```json
{
  "task_signature": "...",
  "page_signature": "...",
  "intent": "click",
  "target_rule": {
    "role": "button",
    "name": "搜索",
    "near_text": "关键词"
  },
  "action_template": {
    "skill": "click"
  },
  "success_evidence": {}
}
```

执行前需要用当前 snapshot 重新解析成最新 ref。

### 12.3 不确定点审批

需要用户审批：

- 是否完全停用 CodeCache。
- DomCache 是否保留只读。
- ActionCache 是否成功后自动写入。

默认建议：

- dp_cli-only 下停用 CodeCache。
- DomCache 短期保留只读，后续迁移。
- ActionCache 成功后自动写，失败自动拉黑。

## 13. 阶段九：ApprovalGate

### 13.1 触发条件

以下情况必须进入审批：

- target confidence 低于阈值。
- 多候选目标难以区分。
- free-form locator。
- 跨域跳转。
- 登录、提交、删除、支付、发布。
- `eval`。
- batch 数量超过阈值。
- extract schema 不确定。
- 连续失败超过 2 次。
- 输出文件覆盖。

### 13.2 审批内容

给用户展示：

```json
{
  "plan": {},
  "candidate_action": {},
  "target": {},
  "confidence": 0.64,
  "risk": "medium",
  "alternatives": [],
  "question": "是否执行该动作？"
}
```

### 13.3 用户可选操作

- approve
- reject
- edit action JSON
- choose alternative ref
- force snapshot
- stop

## 14. 阶段十：旧链路清理

稳定后清理：

- 删除 Python Coder prompt。
- 删除 `python_code` execution mode。
- 删除主链路对 `BrowserActor` 的依赖。
- 删除 CodeCache 执行路径。
- 删除或迁移 `locator_suggestions`。
- 将 `coder_node` 重命名为 `action_builder_node`。
- 将 `observer_node` 拆为 `dp_observer_node`。
- 将 `verifier_node` 改造为 `dp_verifier_node`。

## 15. 推荐实施顺序

### M1：打通 snapshot 到 Coder

- `_dpcli_action_context()` 注入 `dpcli_snapshot_view`。
- Planner prompt 增加 dp_cli snapshot view。
- 保证 Coder 能看到 `e*` 和 `r*` refs。

验收：

- Coder 生成 click/type 时能稳定使用当前 snapshot 的 `e*` ref。
- Coder 生成 extract/list-items 时能稳定使用 `r*` ref。

### M2：新增 DPCLI_ONLY_MODE

- 禁止 Python fallback。
- Executor 只走 dp_cli action。
- HITL 编辑 action JSON。

验收：

- 任意任务不产生 `generated_code`。
- 任意任务不调用 Python 自动化执行器。

### M3：结构化 Planner + TargetSelector

- Planner 输出 JSON plan。
- TargetSelector 输出目标 ref 和置信度。
- 低置信度进入审批。

验收：

- 搜索、点击、提取三个基础任务可稳定执行。
- 多候选目标可触发审批。

### M4：DPVerifier

- 引入 action-specific deterministic verification。
- LLM 仅作为补充。

验收：

- extract 空结果被确定性判失败。
- type 后能验证输入值。
- click 后能验证页面或状态变化。

### M5：缓存迁移

- ActionCache 改为缓存目标规则。
- 禁用 CodeCache 执行链。

验收：

- 缓存不会复用过期 ref。
- 缓存命中后仍会在当前 snapshot 重新解析目标。

### M6：清理 legacy

- 删除旧代码生成链路。
- 删除旧 prompt。
- 重命名节点。

验收：

- 代码中无主链路 Python automation execution。
- 测试全部围绕 dp_cli action。

## 16. 第一批需要审批的设计决策

请优先确认以下决策：

1. `DPCLI_ONLY_MODE=True` 后是否彻底禁止 Python 自动化代码 fallback？
2. `eval` 是否默认禁用？
3. 低置信度阈值是否采用 `0.72`？
4. dp_cli snapshot 是否默认最多传 50 个 interactable elements 给 LLM？
5. CodeCache 是否在 dp_cli-only 模式下直接停用？
6. ref 不存在时，是否禁止自由 locator，改为重新 snapshot 或请求审批？
7. snapshot 失败时，是否禁止 fallback 到旧 DOM Observer？
8. 高风险动作是否全部进入人工审批？

建议答案：

```text
1. 是
2. 是
3. 是，采用 0.72
4. 是，默认 50
5. 是
6. 是
7. 是，dp_cli-only 下禁止 fallback
8. 是
```

## 17. 风险与应对

### 风险 1：dp_cli action 能力不足

如果 dp_cli 缺少某些必要动作，例如按回车、选择下拉框、上传文件、滚动到元素，则 ActionBuilder 会被迫使用 `eval` 或 free-form locator。

应对：

- 优先扩展 dp_cli skill surface。
- 禁止用 LLM Python 代码绕过。
- 缺能力时进入审批并生成 dp_cli 需求。

### 风险 2：snapshot 信息过少

裁剪过度会导致 LLM 看不到目标元素。

应对：

- 支持 `expand`。
- 支持 `find`。
- 支持按目标 hint 动态重取 snapshot。
- 对低置信度目标进入审批。

### 风险 3：ref 过期

`e*` / `r*` 是短期句柄，跨 snapshot 可能失效。

应对：

- 执行前校验 ref 属于当前 snapshot。
- `ref_stale` 自动回 DPObserver。
- 缓存不保存具体 ref，只保存目标规则。

### 风险 4：复杂任务变慢

单 action 循环比一次性 Python 脚本慢。

应对：

- 对列表提取和详情提取使用 `extract` / `batch-detail-extract`。
- 对重复动作沉淀为 dp_cli 内置批处理 skill。
- 不用 Python 脚本换速度，保持结构化可验证。

## 18. 最小完成定义

当以下条件都满足时，认为 dp_cli 全面迁移第一版完成：

- 系统开启 `DPCLI_ONLY_MODE=True` 后可完成打开网页、搜索、点击、提取列表、批量详情提取。
- 所有浏览器操作都通过 `generated_action` 调 `DPCLIExecutor.execute_action()`。
- `generated_code` 全程为空。
- Planner 和 ActionBuilder 均能读取 `dpcli_snapshot_view`。
- 低置信度和高风险动作能进入 ApprovalGate。
- Verifier 能基于 dp_cli result 做确定性判断。
- CodeCache 不再参与执行。
- 旧 Python 执行链可保留但不被主流程调用。
