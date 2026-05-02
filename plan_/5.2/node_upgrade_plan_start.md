# core/nodes.py 拆分计划 — 可行性分析与启动方案

> 基于对当前代码库（commit: 9dbe1cc）的实际分析，对原 `node_upgrade_plan.md` 进行可行性修正和风险提示。
> 生成时间: 2026-05-02

---

## 1. 当前代码实际状态（关键发现）

### 1.1 文件规模
- **实际行数**: 2835 行（原 plan 估计 2600+ 行，实际更大）
- **实际体积**: 约 120KB
- **函数数量**: 30+ 个顶层函数

### 1.2 节点函数存在性核查

| 节点 | 原 plan 假设 | 实际状态 | 行号 | 说明 |
|------|-------------|---------|------|------|
| `cache_lookup_node` | 存在 | ✅ 存在 | 660 | 完整实现 |
| `error_handler_node` | 存在 | ✅ 存在 | 1080 | 完整实现 |
| `observer_node` | 存在 | ✅ 存在 | 1230 | 完整实现 |
| `planner_node` | 存在 | ✅ 存在 | 1806 | 完整实现 |
| `coder_node` | 存在 | ✅ 存在 | 2242 | 完整实现 |
| `executor_node` | 存在 | ✅ 存在 | 2400 | 完整实现 |
| `verifier_node` | 存在 | ✅ 存在 | 2636 | 完整实现 |
| `rag_node` | 存在 | ✅ 存在 | 1642 | 完整实现 |

### 1.3 ✅ 节点完整性确认

所有 8 个被 `graph_v2.py` 导入的节点函数均存在于 `core/nodes.py` 中，**无缺失**。之前分析中怀疑 `rag_node` 缺失是由于 grep 模式问题导致的误判，实际该函数定义于第 1642 行。

---

## 2. 原 plan 的修正评估

### 2.1 整体可行性：✅ 可行

原 plan 的 7 个阶段设计合理，可以直接执行。所有 8 个节点函数均存在，无需前置修复。

### 2.2 需要注意的问题清单

| 优先级 | 问题 | 影响 | 应对策略 |
|--------|------|------|---------|
| **P1** | 当前导入测试在 Windows 环境失败 | 无法运行时验证 | 使用 `py_compile` + AST 分析替代运行时导入 |
| **P2** | nodes.py 实际 2835 行，比估计多 ~200 行 | Phase 工作量增加 | 调整各阶段边界，增加 1-2 个阶段 |

### 2.3 各阶段可行性评估

#### Phase 0：基础包入口 ✅ 可行
- 目标明确，技术路径清晰
- 风险低，建议作为首个 commit

#### Phase 1：迁移 RAG 和 ErrorHandler ✅ 可行
- `rag_node` 完整存在（line 1642），可直接迁移

#### Phase 2：迁移 Cache 和 Locator ✅ 可行
- `cache_lookup_node` 完整存在
- locator 相关 helper 齐全
- 注意：`_handle_cache_failure` 会跳转到 "Planner"，需确认目标模块已加载

#### Phase 3：迁移 Observer ✅ 可行
- `observer_node` 完整，但体积大（~400 行）
- 依赖 dpcli snapshot 函数，需确认是否放 `observer.py` 或 `dpcli.py`

#### Phase 4：迁移 Planner ✅ 可行
- `planner_node` 完整，但逻辑复杂（~270 行）
- 依赖 `_prune_finished_steps` 等 common helper

#### Phase 5：迁移 DP-CLI、Coder、Executor ✅ 可行
- 这是耦合度最高的部分
- **测试依赖**: 3 个测试文件导入了私有 helper，必须保持 re-export

#### Phase 6：迁移 Verifier ✅ 可行
- `verifier_node` 完整，但体积最大（~200 行）
- 依赖 `_handle_cache_failure`，需处理跨模块调用

---

## 3. 修正后的实施计划

### 3.1 启动前检查清单

```bash
# 启动前验证
□ 验证 python -m py_compile core/graph_v2.py 通过
□ 确认所有 graph_v2.py 中导入的符号都在 nodes.py 中定义
```

**启动验收**:
```bash
python -m py_compile core/graph_v2.py
python -m py_compile core/nodes.py
# 注：因 Windows 编码问题，无法直接运行导入测试，使用 AST 检查替代
python -c "import ast; tree = ast.parse(open('core/nodes.py').read()); funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]; print('Nodes found:', [f for f in funcs if 'node' in f])"
```

### 3.2 调整后的阶段划分

| 阶段 | 内容 | 风险等级 | 预计工作量 |
|------|------|---------|-----------|
| **Phase 0** | 建立 core/nodes/ 包入口 | 低 | 2 小时 |
| **Phase 1** | 迁移 error_handler + rag | 低 | 2 小时 |
| **Phase 2** | 迁移 common + locator + verification | 中 | 3 小时 |
| **Phase 3** | 迁移 cache_lookup | 中 | 2 小时 |
| **Phase 4** | 迁移 observer | 中 | 3 小时 |
| **Phase 5** | 迁移 planner | 中 | 2 小时 |
| **Phase 6** | 迁移 dpcli + coder + executor | **高** | 4 小时 |
| **Phase 7** | 迁移 verifier | 中 | 2 小时 |
| **Phase 8** | 清理 core/nodes.py，验证测试 | 中 | 2 小时 |

### 3.3 关键风险与缓解措施

#### 风险 1：循环导入
**缓解**: 严格遵守依赖方向规约（见原 plan 第 7 节），`common.py` 不导入任何节点模块。

#### 风险 2：测试 monkeypatch 失效
**缓解**: 
- 保持 `core/nodes/__init__.py` 的 re-export
- 测试文件暂时不改导入路径
- 拆分完成后再统一调整测试

#### 风险 3：Windows 环境验证困难
**缓解**:
- 使用 `py_compile` 代替运行时导入验证
- 使用 AST 静态分析检查符号存在性
- 关键测试在 Linux 环境补充验证

#### 风险 4：拆分过程中代码冲突
**缓解**:
- 每个阶段独立 commit
- 阶段之间保持可回滚
- 不混入业务逻辑修改

---

## 4. 测试兼容性矩阵

当前测试文件对 `core.nodes` 的依赖：

| 测试文件 | 导入的符号 | 迁移后归属 | 兼容性策略 |
|---------|-----------|-----------|-----------|
| `test_dpcli_action_prompt.py` | `_extract_json_object`, `_validate_dpcli_action`, `coder_node` | `dpcli.py`, `coder.py` | `__init__.py` re-export |
| `test_dpcli_executor_node.py` | `_executor_dpcli_branch` | `executor.py` | `__init__.py` re-export |
| `test_dpcli_observer_projection.py` | `_compact_dpcli_snapshot`, `_observer_dpcli_snapshot`, `_render_dpcli_snapshot_text` | `observer.py` | `__init__.py` re-export |

---

## 5. 启动建议

### 5.1 立即执行（今天）
1. **建立 feature 分支**: `git checkout -b refactor/split-nodes`
2. **Phase 0**: 创建 `core/nodes/` 目录和 `__init__.py`

### 5.2 本周完成
- Phase 1-3：迁移低风险节点（error_handler, rag, common, locator, verification, cache）

### 5.3 下周完成
- Phase 4-7：迁移核心节点（observer, planner, dpcli, coder, executor, verifier）

### 5.4 不建议做的事
- ❌ 不要一次性迁移所有节点
- ❌ 不要在拆分同时修改业务逻辑
- ❌ 不要删除测试中的私有 helper 导入
- ❌ 不要改变 LangGraph 路由方式

---

## 6. 附录：当前 nodes.py 函数分布（按行号）

```
Lines   Function
-------------------------------
26      _get_tab
32      _parse_iso_datetime
47      _is_hit_from_current_task
56      _detect_task_continuity
105     _extract_locator_info
127     _extract_domain_key_from_url
150     _build_step_context
160     _extract_locator_candidates
190     _extract_locators_from_strategies
219     _normalize_locator_token
225     _has_locator_overlap
238     _sanitize_locator
262     _is_valid_element
279     _probe_locator
299     _dry_run_observer_strategies
339     _dry_run_cache_hit_locators
360     _normalize_strategy_list
368     _normalize_failure_scope
373     _normalize_verification_source
379     _build_verification_result
405     _coerce_verification_result
435     _is_failed_verification
439     _parse_verifier_result_content
480     _verification_focus_text
499     _looks_like_global_rewrite_plan
505     _planner_completion_is_premature
535     _planner_forced_extract_plan
553     _count_tokens
562     _get_summarizer_llm
575     _prune_locator_suggestions
597     _prune_finished_steps
660     cache_lookup_node          ← 节点
1080    error_handler_node         ← 节点
1230    observer_node              ← 节点
~1650   _rag_store_kb              (RAG helper)
1766    _rag_store_cache           (RAG helper)
1786    _rag_qa                    (RAG helper)
1806    planner_node               ← 节点
2081    _extract_json_object
2104    _should_use_dpcli_action
2127    _dpcli_action_context
2154    _state_has_dpcli_refs
2165    _validate_dpcli_action
2195    _dpcli_action_coder_node
2242    coder_node                 ← 节点
2294    _dpcli_result_url
2304    _dpcli_error
2309    _dpcli_failure_goto
2317    _executor_dpcli_branch
2400    executor_node              ← 节点
2636    verifier_node              ← 节点
```

---

## 7. 结论

原 `node_upgrade_plan.md` **整体设计合理，方向正确**，所有 8 个节点函数均完整存在，**无需前置修复即可启动拆分**。

需要修正的认知：
1. **rag_node 实际存在**：定义于第 1642 行，之前分析误判为缺失
2. **当前代码比估计更庞大**：2835 行而非 2600 行，各阶段工作量需增加 10-15%
3. **Windows 环境限制**：无法直接运行导入测试，需用 `py_compile` + AST 静态分析替代

**推荐启动顺序**:
```
Phase 0: 包入口 → Phase 1-3: 低风险迁移 → Phase 4-7: 核心迁移 → Phase 8: 清理
```

每个阶段保持独立 commit，随时可回滚。
