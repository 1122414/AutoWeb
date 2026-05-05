# AutoWeb V6 升级路线图

> 生成时间：2026-04-20 | 聚焦：降低成本 + 增强能力

## 总览

本路线图基于对 AutoWeb V6 全代码库的深入分析，围绕**两大核心目标**提出改进方向：
1. **降低成本**：减少 LLM Token 消耗、提升缓存命中率、减少冗余调用
2. **增强能力**：提升定位准确性、验收可靠性、错误恢复能力

### 基线与测量口径

所有收益预估基于以下基线：
- **测试任务集**：10 个典型网页自动化任务（含搜索、点击、表单、爬取、翻页等场景）
- **统计指标**：
  - Token 消耗：各节点 prompt token 数（通过 tiktoken 统计）
  - LLM 调用次数：每任务各节点调用次数
  - 定位成功率：目标元素成功定位 / 总尝试次数
  - 验收误判率：Verifier 错误判定 / 总判定次数
  - 缓存命中率：缓存命中次数 / 总查询次数
- **测量方式**：对比优化前后同一任务集的指标变化（A/B 测试）
- **通过标准**：P50 指标达到预期目标，且 P95 不劣化

---

## Phase 1: 成本优化（预计节省 40%~70%）

### 1.1 Prompt 精简工程（高优先级）

#### Coder Prompt 瘦身
- **问题**：`coder_prompts.py` 包含完整的 `DRISSION_CHEATSHEET` + `TOOLBOX_DESCRIPTION`，每次 cache miss 都完整传入，是系统最长 prompt
- **方案**：
  - 将不变规则迁移到 SystemMessage（仍按次发送），核心收益来自内容裁剪：
    - 压缩 Cheat Sheet 为"常用 10 条"+"参考文档链接"（原完整版占 60%+ token）
    - 仅传入与当前任务相关的 toolbox 函数子集
  - SystemMessage 本身不减少 token（仍按次计费），节省来自 HumanMessage 中移除重复内容
- **预期节省**：Coder 输入 token **20%~40%**
- **验证指标**：对比修改前后，同一批 cache miss 任务的 Coder prompt token 数（目标：降低 ≥20%）

#### Planner Prompt 去重
- **问题**：`planner_prompts.py` 中 `PLANNER_STEP_PROMPT` 和 `PLANNER_CONTINUE_PROMPT` 有大量重复规则，且 few-shot 示例过多
- **方案**：
  - 抽取"规划硬规则"为独立常量，各 prompt 引用而非复制
  - few-shot 从 4 条缩减到 1 条核心示例
  - 将"禁止词"列表从 prompt 内移至 post-processing 过滤层
- **预期节省**：Planner 输入 token **15%~25%**
- **验证指标**：对比修改前后，同一任务 Planner 的平均 prompt token 数（目标：降低 ≥15%）

#### Observer Prompt 压缩
- **问题**：`observer_prompts.py` 中 compressed_list 解压规则、opens_new_tab 判定逻辑占大量 token
- **方案**：
  - `opens_new_tab` 判定保留在 Observer 输出，但精简 Prompt 中判定规则的描述长度（从详细规则改为引用简短说明）
  - 精简 few-shot 示例（保留 2 个核心场景）
- **预期节省**：Observer 输入 token **10%~20%**
- **验证指标**：对比修改前后，同一任务的 Observer prompt token 数（目标：降低 ≥10%）

### 1.2 状态裁剪优化（高优先级）

#### finished_steps 语义去重
- **问题**：`finished_steps` 随轮数线性增长，即使内容相似也全部保留
- **方案**：
  - 引入"步骤指纹"（hash），相似步骤合并为 1 条摘要
  - 相同页面 + 相同操作类型的步骤自动聚类
- **预期节省**：Planner 输入 token **10%~30%**
- **验证指标**：对比修改前后，同一任务的 Planner prompt token 数（目标：降低 ≥10%）

#### 减少 Summarizer 额外调用
- **问题**：`_prune_finished_steps()` 超阈值时会额外调用一次 summarizer LLM
- **方案**：
  - 改为确定性摘要（保留最近 N 条 + 早期步骤压缩为"已完成 X 页数据爬取"）
  - 仅在极端长任务（>20 轮）才启用 LLM 摘要
- **预期节省**：长任务场景 **5%~15%**

### 1.3 Verifier 规则化（中优先级）

- **问题**：除 fatal keyword 规则命中外，仍有大量步骤依赖 LLM 验收，可进一步扩大规则覆盖
- **方案**：
  - 扩展 fatal keyword 列表，覆盖更多确定性失败场景
  - 新增"成功模式匹配"：URL 变化 + 无异常日志 → 直接判定成功
  - 仅在"模糊场景"（无异常但目标未明确达成）才调用 LLM
- **预期节省**：Verifier 调用量减少 **20%~50%**
- **验证指标**：统计 10 个测试任务的 Verifier 调用次数，对比规则化前后的 LLM 调用占比（目标：规则判定覆盖 ≥50% 场景）

### 1.4 缓存命中率提升（高优先级）

#### 引入 L0 热缓存（内存级）
- **问题**：DomCache/CodeCache 依赖 Milvus 查询；Observer 虽有 `_dom_cache`（MD5+context）本地缓存，但未形成跨节点统一的 L0 热缓存
- **方案**：
  - 在单次任务内维护跨节点 `l0_hot_cache`（内存 dict，任务结束即释放）：
    - key: `current_url + dom_hash`
    - value: 最近一次成功的 locator + code（Observer、Coder、Executor 共享）
  - 同一页面重复操作时，Observer 和 CacheLookup 命中路径无需 LLM；整体 LLM 调用显著下降（前提：Planner/Verifier 规则化路径也命中）
- **预期节省**：重复页面操作场景 **30%~60%**
- **验证指标**：对比有无 L0 缓存，同一任务的 LLM 调用次数（目标：重复页面场景减少 ≥30% 调用）

#### 参数替换增强
- **问题**：`extract_param_diffs` 仅做简单 token diff，复杂参数替换失败率高
- **方案**：
  - 引入语义替换（关键词提取 + 同义词匹配）
  - 对 URL 参数、搜索词等常见变量类型做专项处理
- **预期提升**：CodeCache 复用率 **+15%~30%**
- **验证指标**：对比修改前后，同一批相似任务的 CodeCache 命中率（目标：提升 ≥15%）

---

## Phase 2: Observer 增强（定位能力升级）

### 2.1 多候选定位策略（高优先级）

- **问题**：当前链路主要依赖单个高置信 locator，失败后的 fallback 机制较弱（仅重试或回 Observer）
- **方案**：
  - 修改 prompt，要求 LLM 输出 2~3 个候选定位器，附置信度评分
  - 执行层做顺序 dry-run，失败自动回退到下一个候选
  - 全部候选失败时才判定 Observer 失败
- **效果**：复杂页面成功率提升 **+20%~40%**
- **验证指标**：对比单候选 vs 多候选在 10 个不同站点的定位成功率（目标：成功率从基线提升 ≥20%）

### 2.2 语义感知 DOM 压缩（中优先级）

- **问题**：`DOMCompressor` 只压缩"连续同构兄弟节点"，漏掉非连续重复；且 `lite` 模式丢失 ARIA 信息
- **方案**：
  - 结构哈希纳入 `aria-label`、`role`、`data-testid` 等语义属性
  - 对非连续但结构相同的节点做跨子树聚类
  - `lite` 模式增加 `aria-label` 和 `role` 保留
- **效果**：现代前端页面压缩率额外提升 **+10%~20%**，定位准确性提升
- **验证指标**：对比修改前后，同一批现代前端页面的 DOM 压缩后字符数（目标：降低 ≥10%）

### 2.3 本地候选生成层（中优先级）

- **问题**：仍有大量简单场景依赖 LLM；当前仅覆盖唯一文本命中（heuristic）与同上下文缓存（MD5），大量如 "点击登录按钮" 的场景仍走 LLM
- **方案**：
  - 在 `analyze_locator_strategy()` 前扩展"本地候选生成"：
    1. 基于文本的倒排索引：快速定位包含目标文本的元素
    2. 基于 ID/Class 的启发式：优先尝试 `#id` 和 `@@class=`
    3. 基于 ARIA 的语义匹配：利用 `role="button"` + `aria-label`
  - 本地候选唯一且高置信时直接返回，**跳过 LLM**
- **效果**：简单操作 LLM 调用减少 **50%~80%**
- **验证指标**：对比修改前后，同一批简单操作任务（如"点击登录"）的 Observer LLM 调用次数（目标：减少 ≥50%）

### 2.4 生成层 dry-run 增强（中优先级）

- **问题**：当前 `observer_node` 已有 `OBSERVER_DRY_RUN` 校验流程（失败率阈值放行），但仅为单轮整体策略校验；当 LLM 生成多候选策略时，缺乏逐候选过滤机制
- **方案**：
  - 将 dry-run 从"单轮策略整体放行"增强为"逐候选过滤"：每个候选 locator 单独 dry-run（500ms 超时）
  - 失败的候选直接过滤，不进入 `locator_suggestions`
  - 与 DomCache dry-run 统一为同一套验证接口，提升一致性
- **效果**：不可用的定位策略进入后续节点的概率降低 **80%+**
- **验证指标**：对比启用前后，Observer → Coder 链路的定位错误率（目标：降低 ≥50%）

---

## Phase 3: Verifier 增强（验收可靠性升级）

### 3.1 结构化输出替代文本解析（高优先级）

- **问题**：`_parse_verifier_result_content()` 按行前缀解析，模型格式漂移时字段丢失
- **方案**：
  - 改用 JSON Schema 输出：`{status, summary, failure_scope, failed_action, failed_locator, evidence, fix_hint}`
  - 添加 schema 校验，字段缺失时自动补默认值
  - 解析失败时触发二次请求（带格式纠正提示）
- **效果**：验收结果稳定性大幅提升
- **验证指标**：对比文本解析 vs JSON Schema 解析，在 20 个测试用例上的字段完整率（目标：字段完整率从基线提升至 ≥95%）

### 3.2 多信号验收（高优先级）

- **问题**：Verifier 依赖 execution_log 和 current_url，但缺少结构化的"操作前后状态对比"（如 URL 是否变化、DOM 关键元素是否出现）
- **前置实现**：Executor 执行前记录 `pre_action_url` 和 `pre_action_dom_hash` 到 state
- **方案**：
  - 新增验收信号：
    - **URL 变化**：对比 `pre_action_url` 与当前 URL
    - **DOM 变化**：关键元素存在性检测（如"提交后出现成功提示"）
    - **截图对比**：页面截图哈希变化（轻量级视觉验证）
  - 信号权重：fatal error（100%）> URL 变化（80%）> DOM 元素（60%）> 日志分析（40%）
- **效果**：误报率降低 **30%~50%**
- **验证指标**：对比单信号 vs 多信号在 20 个测试用例上的误判率（目标：误判率从基线降低 ≥30%）

### 3.3 Failure Taxonomy 细化（中优先级）

- **问题**：当前只有 `local/global` 二分，指导修复过于粗糙
- **方案**：
  - 细化为 6 类：
    1. `locator_fail`：定位器失效（元素不存在/被隐藏）
    2. `action_mismatch`：执行动作与计划不符
    3. `page_transition_fail`：页面未按预期跳转
    4. `partial_success`：部分完成（如只爬了 5/10 条）
    5. `data_incomplete`：数据提取不完整
    6. `postcondition_fail`：后置条件未满足
  - 每类对应不同的修复策略（注入 Planner prompt）
- **效果**：修复准确率提升 **+25%~40%**
- **验证指标**：对比 local/global 二分 vs 6 类细分，在失败恢复任务中 Planner 下一轮的正确修复率（目标：修复准确率从基线提升 ≥25%）

### 3.4 执行日志摘要化（中优先级）

- **问题**：`log[-2000:]` 直接截断，可能丢失关键错误信息
- **方案**：
  - Executor 输出时自动生成"执行摘要"：
    - 成功操作列表
    - 异常/警告列表
    - 最终页面状态
  - Verifier 消费摘要而非原始日志
- **效果**：Verifier token 降低 **30%~50%**，且信息密度更高
- **验证指标**：对比修改前后，同一任务的 Verifier prompt token 数（目标：降低 ≥30%，且关键错误信息不丢失）

---

## Phase 4: 架构优化（系统性升级）

### 4.1 缓存反馈闭环（高优先级）

- **问题**：DomCache 的 `hit_count`/`fail_count` 和 CodeCache 的 `success_count`/`fail_count` 已建表，但未参与排序和淘汰
- **方案**：
  - DomCache：按 `success_rate = hit_count / (hit_count + fail_count)` 排序，低成功率降权，高成功率升权
  - CodeCache：按 `success_rate = success_count / (success_count + fail_count)` 排序，同上策略
  - 定期清理 `fail_count > 5` 且长期未命中的缓存
- **效果**：缓存质量持续提升，错误复用率降低
- **验证指标**：统计 30 天内各缓存条目的 success_rate 分布（目标：高失败缓存自动降权，缓存整体成功率提升 ≥10%）

### 4.2 统一缓存策略层（中优先级）

- **问题**：DomCache 和 CodeCache 的 stage/gate/threshold/blacklist 逻辑高度重复
- **方案**：
  - 抽取 `CachePolicy` 基类：
    - 统一检索流程（stage1/2/3）
    - 统一阈值管理
    - 统一黑名单集成
    - 统一 dry-run 验证
  - DomCache/CodeCache 只需实现各自的向量字段和领域逻辑
- **效果**：代码可维护性提升，新缓存层（如 L0）易于接入
- **验证指标**：CodeCache 与 DomCache 的重复代码行数（目标：减少 ≥30% 重复逻辑）

### 4.3 节点职责拆分（低优先级）

- **问题**：`observer_node` 同时处理 DOM 捕获、缓存验证、失败回退、策略生成
- **方案**：
  - Observer 拆分为：
    - `dom_capture_node`：纯 DOM 抓取
    - `cache_validate_node`：缓存命中验证
    - `locator_analyze_node`：定位策略生成
  - 同理拆分 Planner：context_prune → route_decision → plan_generation
- **效果**：单节点复杂度降低，可测试性提升
- **验证指标**：先用 `python -m unittest discover -s test -p "test_*.py"` 确保测试通过；若需覆盖率，先安装 `coverage`（`pip install coverage`），再执行：`coverage run -m unittest discover -s test -p "test_*.py"` 然后 `coverage report`，采集基线后设提升目标（如 +20pct 或到 ≥50%）

### 4.4 黑名单前置生效（中优先级）

- **问题**：黑名单是"检索后过滤"，无法减少无效 Milvus 查询
- **方案**：
  - 维护 domain 级"失效候选表"（内存 + Redis）
  - 检索时先过滤 candidate_ids，再查询 Milvus
  - 或直接在 Milvus expr 中排除已失效 cache_id
- **效果**：无效检索减少 **20%~40%**
- **验证指标**：统计 Milvus 查询次数中返回已失效缓存的比例（目标：降低 ≥20%）

---

## Phase 5: 能力边界扩展（长期规划）

### 5.1 视觉理解增强
- **方向**：引入截图 OCR / 元素可见性检测 / 相对位置分析
- **价值**：处理图标按钮、无语义 class、虚拟列表等当前 DOM 分析盲区
- **成本**：可接入本地轻量视觉模型（如 Qwen-VL）降低 API 成本
- **验证指标**：在包含图标按钮的 10 个测试页面上，定位成功率（目标：从基线提升 ≥30%）

### 5.2 本地模型替代
- **方向**：简单任务（文本匹配、规则判定）使用本地小模型（如 Qwen2.5-7B）
- **适用节点**：Verifier 规则化后剩余场景、Observer 本地候选生成
- **价值**：彻底消除简单任务的 API 调用成本
- **验证指标**：本地模型处理的任务数 / 总任务数（目标：本地模型覆盖 ≥40% 场景）

### 5.3 自适应阈值
- **方向**：根据 domain 历史成功率、任务类型、页面复杂度动态调整缓存阈值
- **实现**：在 `CachePolicy` 层引入 `AdaptiveThreshold` 模块
- **价值**：缓存命中率在保持准确性的前提下最大化
- **验证指标**：对比固定阈值 vs 自适应阈值，同一 domain 的缓存命中率（目标：命中率提升 ≥15%，且错误命中率不增加）

---

## 实施优先级矩阵

| 优先级 | 项目 | 预期收益 | 实施难度 | 依赖 |
|--------|------|----------|----------|------|
| **P0** | Coder/Planner Prompt 精简 | 成本-20%~35% | 低 | 无 |
| **P0** | L0 热缓存 | 成本-30%~60% | 低 | 无 |
| **P0** | Verifier 结构化输出 | 可靠性+30% | 中 | 无 |
| **P1** | Verifier 规则化 | 成本-20%~50% | 中 | 无 |
| **P1** | 多候选定位策略 | 成功率+20%~40% | 中 | 无 |
| **P1** | 缓存反馈闭环 | 质量持续提升 | 中 | 无 |
| **P2** | Observer 语义压缩升级 | 成功率+10%，token-10% | 中 | 无 |
| **P2** | 本地候选生成层 | 成本-50%~80%（简单场景） | 中 | 无 |
| **P2** | 多信号验收 | 可靠性+30%~50% | 高 | Verifier 结构化 |
| **P2** | Failure Taxonomy | 修复率+25%~40% | 中 | Verifier 结构化 |
| **P3** | 统一缓存策略层 | 可维护性 | 高 | 反馈闭环 |
| **P3** | 节点拆分 | 可测试性 | 高 | 无 |
| **P4** | 视觉理解 | 能力边界扩展 | 高 | 本地模型 |
| **P4** | 自适应阈值 | 命中率持续提升 | 高 | 反馈闭环 |

---

## 快速启动清单（本周可完成）

- [ ] 1. 裁剪 `DRISSION_CHEATSHEET` 和 `TOOLBOX_DESCRIPTION` 内容（压缩为"常用 10 条"+链接），并将裁剪后的规则从 prompt 模板移至 `SYSTEM_PROMPT` 常量
- [ ] 2. 在各节点调用处引入 `SystemMessage`：
  - `core/nodes.py`：在 `llm.invoke([HumanMessage(...)])` 前添加 `SystemMessage(content=SYSTEM_PROMPT)`
  - `skills/observer.py`：在 `self.llm.invoke(prompt)` 处改为 `self.llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])`
  - 验收标准：Coder prompt token 降低 ≥20%（内容裁剪带来的收益，SystemMessage 角色本身不减 token）
- [ ] 3. 实现 `l0_hot_cache`：单次任务内 URL+DOM hash → locator/code 映射
- [ ] 4. 在 `executor_node` 执行前写入 `pre_action_url` 和 `pre_action_dom_hash` 到 state，`core/state_v2.py` 增加对应字段；同步扩展 Verifier 规则判定：① fatal keyword 列表覆盖更多确定性失败；② 新增确定性成功模式（URL 变化 + 无异常 → 直接成功）
- [ ] 5. 修改 Verifier prompt，要求 JSON 输出并添加解析回退
- [ ] 6. 在 Observer 增加"本地候选生成"：基于 text/id/class 的启发式匹配（aria 需先完成 2.2 的 DOMCompressor 升级）
- [ ] 7. 优化 `finished_steps` 存储：引入指纹去重
- [ ] 8. 埋点改造：在各节点出口处记录结构化日志（节点名、token 消耗、耗时、命中来源、判定类型），用于基线采集和收益验证

---

## 交付标准与回滚策略

### P0 项交付定义（DoD）

| 项目 | 交付标准 | 回滚策略 |
|------|----------|----------|
| Coder/Planner Prompt 精简 | ① HumanMessage token 降低 ≥15%；② 功能回归测试通过；③ 无新增语法/定位错误 | 保留原 prompt 模板为 `_legacy` 版本，配置开关一键回退 |
| L0 热缓存 | ① 重复页面场景 LLM 调用减少 ≥30%；② 无缓存污染导致的错误复用；③ 内存泄漏检测通过 | 配置项 `L0_CACHE_ENABLED` 开关，关闭即回退 |
| Verifier 结构化输出 | ① JSON 解析成功率 ≥95%；② 字段完整率 ≥95%；③ 解析失败时自动回退到文本解析 | 保留 `_parse_verifier_result_content` 原函数作为 fallback |

### 基线采集前置

在启动任何优化项前，必须先完成：
1. **节点级埋点**：在 Observer/Planner/Coder/Verifier/Executor 出口处统一记录：
   - 节点名、调用耗时、prompt token 数、response token 数
   - 命中来源（cache_hit / llm / heuristic）、判定类型（success / fail / retry）
2. **日志格式**：统一 JSON 结构化日志，包含 `task_id`, `node`, `timestamp`, `tokens_in`, `tokens_out`, `hit_source`, `decision`
3. **采集周期**：每项优化前运行 10 个测试任务采集基线，优化后同任务集对比

