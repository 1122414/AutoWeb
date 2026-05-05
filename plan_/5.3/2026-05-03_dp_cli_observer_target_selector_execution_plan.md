# dp_cli Observer 与 TargetSelector 可执行升级计划

生成日期：2026-05-03

适用仓库：`E:\GitHub\Repositories\AutoWeb`

执行对象：opencode / 后续代码实现代理

## 1. 任务目标

本计划只覆盖 `dp_cli` 全面迁移中的关键感知与目标选择层：

- 重做 `DPObserver`，让它成为 `dp_cli snapshot` 的采集、保存、索引、压缩和 Planner View 生成层。
- 新增 `TargetSelector`，让它基于 Planner 的意图，从全量 snapshot/index 中确定具体 ref。
- 建立 full snapshot 永不丢失、Planner 低 token 决策、TargetSelector 可回查全量信息的三层信息架构。
- 参考并吸收现有 `skills/dom_compressor.py` 的相似元素压缩机制，同时优先复用外部 `drissionpage-cli` 已有 snapshot index、data region、pagination、compressor 能力。

本计划不是完整 dp_cli-only 改造，不要求一次性删除旧 Python Coder 链路。

## 2. 任务边界

### 2.1 本阶段必须做

1. 新增或重构 DPObserver 输出结构：
   - `dpcli_agent_view`
   - `dpcli_snapshot_index`
   - `dpcli_observer_diagnostics`
   - `dpcli_snapshot_ref`

2. 全量 snapshot 必须落盘并可回查：
   - full snapshot JSON
   - searchable index JSON
   - compressed/grouped index JSON
   - planner view JSON

3. Observer 必须构建 Planner 可用的页面能力地图：
   - page identity
   - search areas
   - data regions
   - pagination groups
   - forms
   - navigation controls
   - dialogs
   - top-level groups
   - coverage / omitted summary

4. Observer 必须参考相似元素压缩机制：
   - structural hash
   - sibling grouping
   - repeated group compression
   - template + data columns
   - original ref/index traceability

5. 新增 TargetSelector 设计与最小实现：
   - 接收 Planner 输出的 `intent`、`target_hint`、`target_constraints`
   - 通过本地 `SnapshotQueryEngine` 搜索 index/full snapshot
   - 输出确定的 `target_ref`
   - 低置信度或多候选进入后续 ApprovalGate 预留接口

6. Planner 接口预留：
   - Planner 不直接选择 ref
   - Planner 输出结构化 intent 和 target_hint
   - TargetSelector 根据 intent 查找 ref

### 2.2 本阶段严禁做

1. 严禁让 LLM 读取完整 `index.json` 或完整 full snapshot 后“自己打分选元素”。
2. 严禁在 Observer 中使用 LLM 作为权威分区器。
3. 严禁让 Observer 直接生成最终 action。
4. 严禁让 Observer 直接决定最终 target ref。
5. 严禁删除旧 Python Coder 链路。本阶段只新增 dp_cli 新链路能力，避免大爆炸式改造。
6. 严禁把 `dpcli_agent_view` 当作全量信息源。它是 lossy view，权威来源始终是 full snapshot。
7. 严禁缓存或长期复用旧 snapshot 的短期 ref 作为未来执行依据。
8. 严禁为了省 token 删除 full snapshot 信息。
9. 严禁将 `dpcli_observer_diagnostics` 设计成 LLM 总结字段。diagnostics 必须可复现、可测试。
10. 严禁在本阶段引入 `eval`、JS 执行或 Python 自动化 fallback 作为目标选择失败的绕路方案。

## 3. 核心架构原则

### 3.1 三层信息架构

系统必须形成三层信息：

```text
Layer 1: dpcli_agent_view
给 Planner 看。低 token、广覆盖、语义化页面能力地图。

Layer 2: snapshot index / compressed index
给 TargetSelector 和查询引擎用。中等结构化信息，可搜索、可展开。

Layer 3: full snapshot JSON
权威事实源。本地保存，必要时按 ref/subtree 局部读取。
```

### 3.2 正确性优先，成本靠分层降低

正确性优先意味着：

- full snapshot 永不丢。
- 任何 lossy view 都必须能追溯回 full snapshot。
- target ref 必须从当前 snapshot 的权威信息中验证。

成本降低不靠“粗暴删元素”，而靠：

- Planner 只看 agent view。
- TargetSelector 只读取候选局部包。
- LLM 只处理歧义候选，不处理全量 JSON。

### 3.3 Observer 是熵减层，不是决策层

Observer 应回答：

```text
我现在看到了什么？
页面有哪些主要能力？
哪些区域可提取？
哪些控件像分页/搜索/导航/表单？
本次视图覆盖了什么、压缩了什么、遗漏了什么？
```

Observer 不应回答：

```text
下一步一定要点哪个 ref？
最终 action 是什么？
任务是否已经完成？
```

### 3.4 TargetSelector 是目标 ref 确认层

TargetSelector 应回答：

```text
Planner 想做这个 intent 时，当前 snapshot 中哪个 ref 最符合？
这个选择是否唯一、确定、可执行？
如果不确定，候选有哪些，是否需要审批？
```

## 4. 目标数据结构

### 4.1 `dpcli_snapshot_ref`

用途：记录 full snapshot 及其派生文件引用。

建议结构：

```json
{
  "session": "autoweb",
  "snapshot_id": "ss_123",
  "snapshot_seq": 5,
  "page_id": "page_abc",
  "captured_at": "2026-05-03T12:00:00+08:00",
  "page_url": "https://example.com",
  "page_title": "Example",
  "full_snapshot_file": "output/dpcli_snapshots/autoweb/ss_123.full.json",
  "index_file": "output/dpcli_snapshots/autoweb/ss_123.index.json",
  "compressed_index_file": "output/dpcli_snapshots/autoweb/ss_123.compressed_index.json",
  "planner_view_file": "output/dpcli_snapshots/autoweb/ss_123.planner_view.json",
  "hash": "..."
}
```

### 4.2 `dpcli_agent_view`

用途：给 Planner 看，帮助 Planner 决定下一步 intent。

注意：这是 lossy view，不是执行依据。

建议结构：

```json
{
  "page": {
    "url": "...",
    "title": "...",
    "domain": "...",
    "snapshot_id": "ss_123",
    "snapshot_seq": 5,
    "page_id": "page_abc"
  },
  "focus": {
    "mode": "unknown|search|navigation|extract|detail|pagination|form|recovery",
    "confidence": 0.48,
    "reason": "根据任务、当前 URL 和上一步结果推断"
  },
  "capability_map": {
    "search": [],
    "navigation": [],
    "forms": [],
    "data_regions": [],
    "pagination": [],
    "content_regions": [],
    "dialogs": [],
    "primary_actions": []
  },
  "top_level_groups": [],
  "coverage": {
    "total_interactables": 0,
    "shown_representatives": 0,
    "total_data_regions": 0,
    "omitted_groups": []
  },
  "planner_instructions": [
    "只决定下一步 intent 和 target_hint，不要选择具体 ref。",
    "需要具体元素时交给 TargetSelector 查询 full snapshot。"
  ]
}
```

### 4.3 `dpcli_snapshot_index`

用途：state 中只放索引摘要和文件引用，不放全量 index。

建议结构：

```json
{
  "snapshot_id": "ss_123",
  "full_snapshot_file": "...full.json",
  "index_file": "...index.json",
  "compressed_index_file": "...compressed_index.json",
  "lookup_manifest": {
    "by_ref": true,
    "by_role": true,
    "by_text": true,
    "by_region": true,
    "by_structural_group": true
  },
  "summary": {
    "elements": 180,
    "containers": 96,
    "regions": 6,
    "structural_groups": 14,
    "inputs": 3,
    "buttons": 12,
    "links": 120
  },
  "top_level_groups": [
    {
      "group_id": "g_search_1",
      "kind": "search_area",
      "count": 2
    }
  ]
}
```

### 4.4 `dpcli_observer_diagnostics`

用途：记录 Observer 本轮处理质量、压缩策略、不确定性和风险。

必须程序化生成，不能依赖 LLM。

建议结构：

```json
{
  "snapshot_ok": true,
  "raw_nodes": 1260,
  "interactables": 180,
  "containers": 96,
  "data_regions_detected": 5,
  "pagination_groups_detected": 1,
  "structural_groups_detected": 14,
  "planner_view_mode": "coverage_first",
  "compression": {
    "strategy": "structural_sibling_hash",
    "min_group_size": 3,
    "largest_group_count": 100,
    "groups_collapsed": 14
  },
  "coverage": {
    "full_snapshot_preserved": true,
    "planner_view_lossy": true,
    "recoverable_from_full_snapshot": true
  },
  "uncertainty": {
    "task_focus_unclear": true,
    "ambiguous_regions": [],
    "ambiguous_pagination": false
  },
  "warnings": []
}
```

## 5. Observer 分区设计

### 5.1 分区依据

Observer 必须基于 snapshot 事实字段进行分区：

- `ref`
- `ref_type`
- `parent_ref`
- `children_map`
- `role`
- `tag`
- `name`
- `text`
- `href`
- `xpath`
- `visibility`
- `bounds`
- `states`
- `context`

不得使用 LLM 作为权威分区来源。

### 5.2 分区类型

Observer 至少识别以下区域：

```text
search_area
data_region
pagination
navigation
form
dialog
content_region
primary_action
repeated_structure
```

### 5.3 data_regions 生成逻辑

候选 container：

```text
ref_type == container
有 xpath
descendant element 数 >= 3
```

判定为 data region 的依据：

```text
内容链接数量 >= 3
或重复 row group 数量 >= 3
或 table/list/grid/ul/ol/main/section 内存在重复结构
```

`item_count_estimate`：

```text
max(content_link_count, repeated_row_group_count, structural_group_count)
```

`kind` 判定：

```text
table: tag=table 或 role=table/grid
list: role=list/listbox 或 descendants 有 li
card_grid: 内容链接 >= 3 且 row group >= 3
repeated_structure: 其他重复结构
```

`region_label` 生成优先级：

```text
container.name
aria-label / title
附近 heading
父级 landmark 名称
sample item 共同特征
role/tag fallback
```

如果没有明确 label，输出：

```json
{
  "region_label": "未命名列表区域",
  "label_confidence": "low"
}
```

`available_actions`：

```text
有 container/group ref -> expand
item_count >= 3 -> list-items
kind in table/list/card_grid/repeated_structure -> extract
存在 detail links -> batch-detail-extract_candidate
```

### 5.4 pagination 生成逻辑

候选控件：

```text
role in {"button", "link"}
或 tag in {"a", "button"}
或 interactable_now == true
```

识别依据：

```text
name/text/aria-label/title 包含 下一页、上一页、next、prev、previous、forward
class/id/href 包含 page-next、page-prev、pagination、next、prev
文本是纯数字，且兄弟节点中有多个数字
同父节点下同时存在数字页码和 next/prev
href 中出现 page=、/page/、p= 等分页模式
disabled/aria-disabled/classes 标记不可用
```

输出示例：

```json
{
  "group_id": "g_pagination_1",
  "controls": [
    {
      "label": "上一页",
      "direction": "prev",
      "enabled": false,
      "kind": "button_or_link"
    },
    {
      "label": "1",
      "direction": "page_number",
      "current": true,
      "enabled": true
    },
    {
      "label": "下一页",
      "direction": "next",
      "enabled": true,
      "kind": "button_or_link"
    }
  ],
  "evidence": [
    "sibling numeric page controls",
    "next keyword in text/name",
    "same parent container"
  ],
  "available_actions": ["click"]
}
```

### 5.5 top_level_groups 生成逻辑

`top_level_groups` 不由 LLM 生成，而由多个 detector 合并生成。

`search_area`：

```text
存在 role=search ancestor
或 form 内有 searchbox/textbox + submit/search button
或 input placeholder/name/text 包含 搜索/search/keyword
且附近有 button/link 文本为 搜索/search
```

`repeated_data_items`：

```text
来自 data_regions
或 structural compressor 发现同父级下 >= 3 个相似结构
或 row_groups >= 3
或 content_links >= 3
```

`pagination`：

```text
同一父容器下有 next/prev/page number 控件
或至少 2 个数字页码 + 一个 next/prev
或控件 href/class/id 呈现 page pattern
```

group id 生成：

```text
g_{kind}_{ordinal}
```

或更稳定：

```text
g_{kind}_{hash(parent_ref_or_xpath_template)[:6]}
```

## 6. 相似元素压缩设计

### 6.1 参考对象

必须参考现有：

- `skills/dom_compressor.py`
- 外部 `E:\GitHub\Repositories\drissionpage-cli\dp_cli\compressor.py`

核心思想：

```text
相似兄弟节点
-> structural hash
-> group
-> template + data columns
-> sample refs
-> original index/ref traceability
```

### 6.2 压缩输入

dp_cli snapshot records，而不是旧 DOM 节点。

每个 record 至少包含：

```text
ref
ref_type
tag
role
input_type
parent_ref
xpath
name
text
href
children refs
```

### 6.3 structural hash

hash 应考虑：

```text
tag
role
input_type
稳定 id
直接子节点结构
子节点 role/tag 序列
```

不应考虑：

```text
具体文本
具体 href
动态序号
短期 ref
```

### 6.4 compressed group 输出

建议结构：

```json
{
  "group_id": "g_rank_items_1",
  "type": "compressed_ref_group",
  "kind": "repeated_data_items",
  "count": 100,
  "template": {
    "role_pattern": ["link", "text", "text"],
    "path_template": "main > list > item[{i}]",
    "region_ref": "r5"
  },
  "data": {
    "text": ["第1名 斗破苍穹", "第2名 ..."],
    "href": ["...", "..."],
    "_ref": ["e21", "e24", "e27"],
    "_index": [1, 2, 3]
  },
  "samples": [
    {"ref": "e21", "text": "第1名 斗破苍穹"},
    {"ref": "e24", "text": "第2名 ..."}
  ],
  "available_actions": ["extract", "list-items"]
}
```

## 7. TargetSelector 设计

### 7.1 职责

TargetSelector 根据 Planner 的结构化意图，从当前 snapshot 的权威信息中确定具体 ref。

它不应该把全量 `index.json` 喂给 LLM。

它应该使用：

```text
SnapshotQueryEngine
+ 必要时的小候选包 LLM 裁决
+ full snapshot ref 验证
```

### 7.2 输入

```json
{
  "intent": "click",
  "target_hint": "搜索按钮",
  "target_constraints": {
    "role": ["button"],
    "near": "搜索输入框",
    "text_or_name": ["搜索", "Search"]
  },
  "snapshot_ref": {
    "full_snapshot_file": "...full.json",
    "index_file": "...index.json",
    "compressed_index_file": "...compressed_index.json"
  }
}
```

### 7.3 SnapshotQueryEngine API 预留

必须预留以下接口：

```python
search_snapshot(query: dict) -> list[dict]
get_ref(ref: str) -> dict | None
get_region(region_ref: str) -> dict | None
expand_group(group_id: str, limit: int = 20) -> list[dict]
find_by_text(text: str, scope: dict | None = None) -> list[dict]
find_near(ref_or_text: str, query: dict) -> list[dict]
load_subtree(ref: str, depth: int = 2) -> dict
verify_ref(ref: str, intent: str) -> dict
```

### 7.4 TargetSelector 工作流程

1. Compile Query

将 Planner 输出转成查询条件：

```text
intent
target_hint
role constraints
text/name constraints
scope constraints
near constraints
required ref_type
```

2. Scope Narrowing

按 intent 缩小搜索空间：

```text
click/type -> element records
extract/list-items/expand -> region/group records
pagination -> pagination groups
search -> search/form groups
```

3. Deterministic Retrieval

本地查询：

```text
exact name/text
role match
aria/placeholder/value
nearby_text
parent/region
path/group
visible/enabled
ref type
```

4. Group-Aware Expansion

如果命中 compressed group：

```text
展开 group refs
或读取 group 对应 full subtree
或按 group kind 选择 container ref
```

5. Candidate Pack

生成 1-8 个候选的小包：

```json
{
  "query": {
    "intent": "click",
    "target_hint": "搜索按钮"
  },
  "candidates": [
    {
      "ref": "e12",
      "role": "button",
      "name": "搜索",
      "text": "搜索",
      "visible": true,
      "enabled": true,
      "region": "search_form",
      "nearby_text": ["请输入关键词"],
      "why_matched": ["exact_name", "role_button", "near_search_input"]
    }
  ],
  "conflicts": []
}
```

6. Deterministic Selection

如果只有一个强匹配：

```json
{
  "status": "selected",
  "selection_mode": "deterministic",
  "target_ref": "e12",
  "confidence": 1.0
}
```

7. LLM Candidate Arbitration

只有多个候选冲突时，才调用 LLM。

LLM 输入只能是 candidate pack，不是完整 index。

8. Full Snapshot Verification

最终选中 ref 后，必须读取 full snapshot 验证：

```text
ref 存在
ref 属于当前 snapshot
ref_type 匹配 intent
visible/enabled
不是 stale ref
```

9. ApprovalGate 预留

以下情况必须进入审批接口：

```text
候选不唯一
候选语义冲突
无法验证 ref
需要 free-form locator
低置信度
高风险动作
```

### 7.5 TargetSelector 输出

```json
{
  "status": "selected|need_approval|not_found|need_more_observation",
  "target_ref": "e12",
  "target_kind": "element",
  "skill_hint": "click",
  "selection_mode": "deterministic|llm_arbitrated|user_approved",
  "evidence": {
    "role": "button",
    "name": "搜索",
    "nearby_text": ["请输入关键词"],
    "source": "full_snapshot"
  },
  "alternatives": [],
  "approval_required": false
}
```

## 8. Planner 接入要求

Planner 不应选择具体 ref。

Planner 应输出：

```json
{
  "status": "continue",
  "intent": "click",
  "target_hint": "搜索按钮",
  "target_constraints": {
    "role": ["button"],
    "near": "搜索输入框",
    "text_or_name": ["搜索", "Search"]
  },
  "expected_evidence": "页面进入搜索结果页"
}
```

如果 Planner 不确定，应输出：

```json
{
  "status": "need_more_observation",
  "inspect_request": {
    "scope_hint": "main content",
    "question": "需要确认主内容区是否存在可提取列表"
  }
}
```

## 9. 开发优先级

### P0：只读重构与 artifact 保存

目标：不改变主流程行为，先把 full snapshot 保存和派生视图生成做出来。

任务：

- 新增 `SnapshotStore`
- 保存 full snapshot
- 保存 index
- 保存 planner view
- 保存 compressed index
- state 中写入 `dpcli_snapshot_ref`

验收：

- 每次 dp_cli snapshot 后，本地生成 4 个 artifact。
- artifact 可由 snapshot_id 找回。

### P1：Observer Agent View

目标：让 Observer 输出稳定的 `dpcli_agent_view`。

任务：

- 实现 data region 投影。
- 实现 pagination 投影。
- 实现 search/form/navigation/dialog 分区。
- 实现 top_level_groups。
- 实现 coverage 和 omitted summary。

验收：

- Planner 可看到页面能力地图。
- 视图不包含完整元素海洋。
- full snapshot 可恢复所有 omitted 信息。

### P2：Structural Compression

目标：参考 `dom_compressor.py` 和外部 `dp_cli/compressor.py` 实现 snapshot record 压缩。

任务：

- structural hash
- sibling group
- compressed_ref_group
- template + data columns
- ref/index traceability

验收：

- 重复列表被压缩为 group。
- group 能展开回原始 refs。
- Planner view 中列表只显示 group 摘要和 samples。

### P3：SnapshotQueryEngine

目标：为 TargetSelector 提供本地查询能力。

任务：

- `search_snapshot`
- `get_ref`
- `expand_group`
- `find_by_text`
- `find_near`
- `verify_ref`

验收：

- 不调用 LLM 即可找到明确的搜索按钮、下一页按钮、主列表区域。
- 查询结果只返回小候选包。

### P4：TargetSelector

目标：接入 Planner intent，输出具体 ref。

任务：

- intent -> query 编译
- deterministic selection
- candidate conflict detection
- LLM arbitration 预留
- ApprovalGate 预留
- full snapshot ref verification

验收：

- 单一明确目标不调用 LLM。
- 多候选冲突返回 `need_approval` 或调用候选包仲裁。
- 不读取完整 index 给 LLM。

### P5：Planner Prompt 接入

目标：让 Planner 读取 `dpcli_agent_view`，输出结构化 intent。

任务：

- 新增 dp_cli Planner prompt。
- 不再依赖 `Visual Suggestions / locator_suggestions`。
- 输出 JSON plan。

验收：

- Planner 输出 intent/target_hint。
- Planner 不输出具体 ref。

## 10. 测试要求

至少新增测试：

```text
test_dpcli_snapshot_store.py
test_dpcli_observer_agent_view.py
test_dpcli_structural_compression.py
test_dpcli_pagination_detector.py
test_dpcli_data_region_detector.py
test_snapshot_query_engine.py
test_target_selector.py
```

关键测试场景：

- 搜索页：识别 search_area。
- 列表页：识别 data_region 和 repeated_data_items。
- 分页页：识别 pagination group 和 next control。
- 多搜索按钮：TargetSelector 发现冲突。
- 重复卡片列表：compressed group 可展开。
- full snapshot 保存后可按 ref 回查。
- Planner view 不包含全量 index，但能说明 omitted 信息可恢复。

## 11. 后续接口预留

### 11.1 ApprovalGate

预留字段：

```json
{
  "approval_required": true,
  "approval_reason": "...",
  "alternatives": []
}
```

### 11.2 ActionBuilder

TargetSelector 输出应能直接供 ActionBuilder 使用：

```json
{
  "intent": "click",
  "target_ref": "e12",
  "skill_hint": "click"
}
```

### 11.3 DPVerifier

Observer 和 TargetSelector 应保留 evidence，供 Verifier 使用：

```json
{
  "expected_evidence": "...",
  "target_evidence": {},
  "snapshot_id": "..."
}
```

### 11.4 Cache

后续 ActionCache 不应缓存短期 ref，应缓存：

```text
intent
target rule
page signature
success evidence
```

## 12. 推荐文件布局

建议新增：

```text
skills/dpcli_snapshot_store.py
skills/dpcli_snapshot_indexer.py
skills/dpcli_planner_view.py
skills/dpcli_snapshot_query.py
core/nodes/target_selector.py
prompts/dpcli_planner_prompts.py
prompts/target_selector_prompts.py
```

可选后续重命名：

```text
core/nodes/observer.py -> dp_observer.py
core/nodes/coder.py -> action_builder.py
```

本阶段不强制重命名，避免扩大改动范围。

## 13. 完成定义

本阶段完成时必须满足：

- full snapshot 已落盘并可回查。
- Observer 输出 `dpcli_agent_view`、`dpcli_snapshot_index`、`dpcli_observer_diagnostics`。
- `dpcli_agent_view` 可供 Planner 做 intent 决策。
- data region、pagination、search area、top_level_groups 均由确定性规则生成。
- 相似元素压缩保留 ref/index 可追溯性。
- TargetSelector 能通过本地查询引擎确定明确目标 ref。
- LLM 不读取全量 index/full snapshot。
- diagnostics 不依赖 LLM。
- 旧 Python Coder 链路未被删除，且本阶段未扩大执行链路风险。
