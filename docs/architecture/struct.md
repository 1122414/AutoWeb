# AutoWeb V6 架构全览

> 生成时间：2026-04-20 | Commit: 72eadf9

## 1. 系统定位

AutoWeb 是一个基于 **LangGraph** 的多节点协作 AI Agent，核心能力：
- **自然语言理解**：接收用户指令，自动拆解为网页操作任务
- **双层缓存加速**：DomCache(L1) + CodeCache(L2)，实现经验复用
- **Human-in-the-Loop**：Executor 前中断 + Verifier 后中断，支持人工审批/编辑
- **RAG 知识库**：支持将爬取数据存入 Milvus 或进行 QA 问答

## 2. 节点流转图

```
START
  │
  ▼
Observer ──[DomCache Hit]──► Planner
                               │
                    [RAG Task] │ [Action Task]
                               │        │
                               ▼        ▼
                            RAGNode  CacheLookup
                               │        │
                               ▼        │
                            Observer    │ [CodeCache Hit]
                                        │        │
                                        │        ▼
                                        │     Executor ──► Verifier
                                        │                 │
                                        │    [CodeCache Miss]
                                        │        │
                                        │        ▼
                                        │     Coder ──► Executor
                                        │
                                         │[Success & no_store]
                                         │        │
                                         │        ▼
                                         │     Observer
                                         │        ▲
                                         │        │[Success & needs_store]
                                         │     RAGNode
                                         │
                               [Fail (cache)]
                                        │
                                        ▼
                                     Planner
                                        │
                              [Fail (LLM)]
                                        │
                                        ▼
                                     Observer
                                        │
                              [Done]    │
                                        ▼
                                        END
```

**核心流转规则**：
- **Observer**：唯一入口，负责 DOM 捕获 → 定位策略生成
- **Planner**：主要出口（`__end__`），负责任务完成判定 + 下一步规划
- **CacheLookup**：代码缓存检索，命中则跳过 Coder
- **RAGNode**：独立知识库节点，完成后回 Observer
- **Verifier**：执行后验收；成功后分两支：无缓存写入需求直回 Observer，有写入需求先到 RAGNode 再回 Observer；local 失败回 Observer 重试，global 失败回滚策略后回 Observer；缓存代码失败走 `_handle_cache_failure` 跳 Planner
- **ErrorHandler**：全局错误兜底，回 Observer 或 `__end__`（严重错误时直接终止）

## 3. 节点职责详述

### Observer（感知节点）
- **输入**：浏览器标签页、用户任务、历史步骤、失败记录
- **处理**：
  1. 注入 JS 捕获 DOM 骨架
  2. DOMCompressor 压缩（重复列表折叠为 `compressed_list`）
  3. DomCache 检索（4 向量字段，主检索流程：task 粗筛 → dom+step 精排；url 用于归一化/域过滤）
  4. Dry-run 验证命中缓存的定位策略
  5. 未命中则调用 LLM 生成定位策略
- **输出**：`locator_suggestions`（定位策略列表）
- **优化**：启发式文本匹配（唯一文本直接返回，跳过 LLM）+ MD5 内存缓存

### Planner（决策节点）
- **输入**：当前 URL、已完成步骤、定位建议、验证结果、失败反思
- **处理**：
  1. 任务完成判定（最高优先级）
  2. 基于证据的规划（只允许规划 Observer 实际观察到的元素）
  3. 跨页面操作拆分（搜索和点击必须分开）
  4. 连续失败检测与强制跳过
- **输出**：下一步计划文本
- **关键约束**：禁止"随后""然后"等跨页面操作词

### CacheLookup（缓存检索节点）
- **输入**：用户任务、当前计划、URL、定位信息
- **处理**：
  1. CodeCache 多向量检索（字段：goal + url + locator + task；主流程：user_task+url 粗召回 → task 门控 → goal 精排）
  2. 参数差异提取与替换、locator 信息用于重复检测
  3. Dry-run 探测缓存代码中的定位器
- **输出**：缓存命中的代码 或 空（触发 Coder）

### Coder（代码生成节点）
- **输入**：计划文本、定位策略
- **处理**：LLM 将 XPath 策略转化为 Python 执行代码
- **输出**：可执行的 Python 代码字符串
- **约束**：禁止实例化 ChromiumPage、必须用 toolbox 保存数据、字段级 try-except

### Executor（执行节点）
- **输入**：生成的代码、浏览器实例
- **处理**：
  1. 代码安全检查（CodeGuard）
  2. 沙箱执行（捕获 stdout/stderr）
  3. 错误分类：语法错误、定位错误、运行时错误
- **输出**：执行日志

### Verifier（验收节点）
- **输入**：执行日志、当前计划、用户任务
- **处理**：
  1. 致命错误快速判定（Runtime Error / Traceback / ElementNotFound）
  2. LLM 语义验收（对比计划目标与实际结果）
  3. 提取 failure_scope / failed_action / failed_locator / fix_hint
- **输出**：验收结果（success/fail + 详细诊断信息）
- **HITL**：Verifier 后中断，支持人工覆盖结果

### RAGNode（知识库节点）
- **输入**：任务类型（store_kb / store_cache / qa）
- **处理**：Milvus 向量库操作（数据入库、缓存入库或问答检索）
- **输出**：操作结果摘要

## 4. 双层缓存系统

### L1: DomCache（DOM 策略缓存）
| 维度 | 说明 |
|------|------|
| 向量字段 | url_vector, dom_vector, task_vector, step_vector |
| 检索流程 | stage2(task 粗筛) → stage3(dom+step 精排) |
| 命中后 | Dry-run 验证 locator_suggestions，失败则标记失效 |
| 去重 | _is_duplicate 检查，score ≥ threshold 时跳过保存 |
| TTL | 默认 168 小时 |

### L2: CodeCache（代码动作缓存）
| 维度 | 说明 |
|------|------|
| 向量字段 | goal_vector, locator_vector, user_task_vector, url_vector |
| 检索流程 | stage1(user_task+url 粗召回) → stage2(task 门控) → stage3(goal 精排) |
| 命中后 | 参数差异替换 + dry-run 探测，直接进 Executor |
| 黑名单 | 失败缓存软删除（Redis/本地降级），TTL 到期自动解除 |

### 缓存反馈闭环
- DomCache 字段：hit_count / fail_count（已建表，但未在运行时持续回写）
- CodeCache 字段：success_count / fail_count（已建表，但未在运行时持续回写）
- 当前问题：统计字段已建表，但运行时主要通过 `record_failure` + 软黑名单处理失败，未形成计数自增闭环

## 5. 状态管理

```python
AgentState = EnvState + TaskState

EnvState:
  - current_url: 当前页面 URL
  - dom_skeleton: DOM 骨架（压缩后）
  - locator_suggestions: 定位策略列表（clearable_list_reducer）
  - dom_hash: DOM MD5 哈希（变化检测）

TaskState:
  - user_task: 原始用户任务
  - plan: 当前计划
  - finished_steps: 已完成步骤列表（clearable_list_reducer）
  - reflections: 失败反思列表（clearable_list_reducer）
  - is_complete: 任务是否完成
  - loop_count: 防死循环计数

AgentState 扩展字段（运行态控制）:
  - verification_result: 验收结果（Verifier 节点写入）
  - error: 错误信息（ErrorHandler 节点写入）
  - _task_started_at: 任务启动时间（main.py 在任务开始时写入 ISO 时间，用于 DomCache/CodeCache 同任务缓存隔离：避免读到本任务刚写入的缓存）
  - generated_code: Coder 生成的代码
  - execution_log: Executor 执行日志

缓存控制字段:
  - _code_source: 代码来源（"cache" | "llm"）
  - _cache_failed_this_round: 本轮缓存是否已失败（防死循环）
  - _cache_hit_id: CodeCache 命中记录 ID
  - _failed_code_cache_ids: 本轮禁用 CodeCache ID 列表
  - _observer_source: 观察来源（"dom_cache" | "observer"）
  - _dom_cache_hit_id: DomCache 命中记录 ID
  - _failed_dom_cache_ids: 本轮禁用 DomCache ID 列表

重试与失败控制:
  - coder_retry_count: Coder 重试计数（语法错误时微循环，最多 3 次）
  - error_type: 错误类型（"syntax" | "locator" | "security" | "security_max_retry" | "syntax_max_retry" | "critical"）
  - _step_fail_count: 连续步骤失败计数（成功时重置为 0）

RAG 与 HITL:
  - rag_task_type: RAG 任务类型（"store_kb" | "store_cache" | "qa"）
  - hitl_mode: HITL 模式（"off" | "review_all"）
```

**关键 reducer 设计**：
- `clearable_list_reducer`：支持 `None` 清空列表，支持 `__replace__` 强制替换
- `add_messages`：LangGraph 内置消息追加

## 6. 关键数据流

### 正常流程（每轮循环）
1. Observer 捕获 DOM → 生成定位策略
2. Planner 判定完成 / 生成计划
3. CacheLookup 检索代码缓存
   - [Dry-run 失败] → 标记缓存失效 → 回 Observer
4. [Cache Miss] Coder 生成代码 → Executor 执行
   - [语法错误] → Coder 微循环重试（最多 3 次）
   - [定位错误/严重错误] → ErrorHandler
5. Verifier 验收 → 成功后按需保存缓存（满足条件时走 RAGNode），否则直接进入下一轮
6. 回到 1

### 缓存命中流程
1. Observer DomCache 命中 → 复用定位策略
2. Planner 生成计划
3. CacheLookup CodeCache 命中 → 复用代码
4. Executor 直接执行
5. Verifier 验收
6. 回到 1

### 失败恢复流程
1. Verifier 判定失败
2. 提取 failure_scope（local/global）
3. local：回 Observer 重试，保留上下文，定向修复失败定位器
4. global：回滚最近一条 locator suggestion，回 Observer 重新分析页面
5. cache 失败：`_handle_cache_failure` 失效缓存 + 跳 Planner 重新规划
6. 严重错误：`error_handler_node` 直接 `goto="__end__"` 终止任务

## 7. Token 消耗结构

| 节点 | 调用频率 | Prompt 长度 | 主要消耗来源 |
|------|----------|-------------|-------------|
| Planner | 每轮 1 次 | 高 | finished_steps + suggestions + reflections + verification |
| Coder | cache miss 时 | 最高 | cheat sheet + toolbox + xpath_plan |
| Observer | DOM 变化或失败恢复时 | 中高 | dom_json（压缩后仍大）+ few-shot examples |
| Verifier | 每执行 1 次 | 中 | execution_log[-2000:] |
| Summarizer | 阈值触发 | 中 | _prune_finished_steps 额外调用 |

**已有优化**：
- DOMCompressor：列表页 token 降低 30%~80%
- _prune_locator_suggestions：保留最近 N 组策略
- _prune_finished_steps：tiktoken 监控，超阈值摘要化
- RemoveMessage：定向删除历史消息

## 8. 关键文件映射

| 职责 | 文件 | 行数 |
|------|------|------|
| 图构建 | core/graph_v2.py | 70 |
| 节点实现 | core/nodes.py | 2332 |
| 状态定义 | core/state_v2.py | 105 |
| LLM 工厂 | core/llm_factory.py | ~50 |
| DOM 分析 | skills/observer.py | 281 |
| DOM 压缩 | skills/dom_compressor.py | 256 |
| 代码缓存 | skills/code_cache.py | 497 |
| DOM 缓存 | skills/dom_cache.py | 441 |
| 浏览器驱动 | drivers/drission_driver.py | 116 |
| RAG 检索 | rag/retriever_qa.py | 592 |
| Planner Prompt | prompts/planner_prompts.py | 157 |
| Coder Prompt | prompts/coder_prompts.py | 155 |
| Observer Prompt | prompts/observer_prompts.py | 94 |
| Verifier Prompt | prompts/verifier_prompts.py | 43 |
