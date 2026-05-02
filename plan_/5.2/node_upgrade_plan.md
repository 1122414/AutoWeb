# core/nodes.py 拆分计划

> 目标：在不改变 LangGraph 节点外部接口的前提下，把当前单文件 `core/nodes.py` 拆成可维护的节点包，降低后续修改 Observer、Cache、DP-CLI、Verifier 时的冲突和心智负担。

## 1. 当前判断

当前 `core/nodes.py` 约 120KB、2600+ 行，已经同时承担四类职责：

- Graph 节点入口：`observer_node`、`planner_node`、`cache_lookup_node`、`rag_node`、`coder_node`、`executor_node`、`verifier_node`、`error_handler_node`
- 跨节点通用工具：时间解析、URL/domain 提取、token 统计、finished_steps 裁剪
- 领域工具：locator dry-run、verification 结果归一化、cache 保存/失败处理、DP-CLI action/snapshot
- 测试可见的私有 helper：例如 `_extract_json_object`、`_validate_dpcli_action`、`_executor_dpcli_branch`

结论：需要拆分，但不建议机械地“每个节点一个 py 文件”。更适合按职责域拆，保留 `core.nodes` 这个稳定导入入口。

## 2. 拆分原则

1. 外部导入路径先保持不变：
   - `from core.nodes import observer_node, planner_node, ...` 继续可用
   - 测试里暂时使用的私有 helper 也先通过 `core.nodes` re-export
2. 不改 LangGraph 路由方式：
   - 继续由节点返回 `Command(goto="NodeName")`
   - 不引入 `add_conditional_edges`
3. 先迁移低耦合模块，再迁移高耦合模块：
   - RAG、ErrorHandler 风险最低
   - Cache、Observer 次之
   - Coder、Executor、Verifier 和 DP-CLI/verification helper 耦合更高，放后面
4. 拆分过程只做搬迁，不顺手重构业务逻辑：
   - 第一阶段目标是结构稳定
   - 逻辑优化另开后续任务

## 3. 目标目录结构

```text
core/
  nodes/
    __init__.py          # 统一对外导出，兼容原 core.nodes 导入
    common.py            # 通用 helper：config/browser/time/url/token/step pruning
    locator.py           # locator 提取、归一化、dry-run
    verification.py      # verification 结果构造、解析、失败判断、focus text
    cache.py             # cache_lookup_node + cache 保存/失败处理
    error_handler.py     # error_handler_node
    observer.py          # observer_node + DP-CLI snapshot projection
    rag.py               # rag_node + _rag_store_kb/_rag_store_cache/_rag_qa
    planner.py           # planner_node + planner completion/forced plan helper
    dpcli.py             # DP-CLI action/snapshot 共用工具
    coder.py             # coder_node + DP-CLI action coder branch
    executor.py          # executor_node + DP-CLI executor branch/error mapping
    verifier.py          # verifier_node
```

说明：

- `core/nodes.py` 文件最终应删除或改成兼容 shim，但不能和 `core/nodes/` 目录长期并存同名冲突。
- Python 同一目录下同时存在 `nodes.py` 和 `nodes/` 包时，导入解析容易产生歧义，迁移时应一次性完成“文件改包”的最小闭环。

## 4. 建议迁移映射

| 原位置 | 新位置 | 备注 |
|---|---|---|
| `_get_tab` | `core/nodes/common.py` | Browser/config 注入工具 |
| `_parse_iso_datetime`、`_is_hit_from_current_task` | `common.py` | Cache/任务连续性共用 |
| `_detect_task_continuity` | `common.py` | Observer 入口使用 |
| `_extract_domain_key_from_url`、`_build_step_context` | `common.py` | Cache 保存使用 |
| `_count_tokens`、`_get_summarizer_llm`、`_prune_finished_steps` | `common.py` | Planner/上下文裁剪 |
| `_extract_locator_info`、`_extract_locator_candidates` | `locator.py` | CodeCache embedding / dry-run |
| `_extract_locators_from_strategies`、`_sanitize_locator` | `locator.py` | Observer/Cache locator 校验 |
| `_probe_locator`、`_dry_run_observer_strategies`、`_dry_run_cache_hit_locators` | `locator.py` | 依赖浏览器对象，仍保持纯函数 |
| `_normalize_failure_scope`、`_build_verification_result`、`_coerce_verification_result` | `verification.py` | Executor/Verifier/Cache failure 共用 |
| `_parse_verifier_result_content`、`_verification_focus_text` | `verification.py` | Verifier 主逻辑使用 |
| `cache_lookup_node`、`_save_code_to_cache`、`_save_dom_to_cache` | `cache.py` | Cache 域内聚 |
| `_record_cache_failure`、`_handle_cache_failure` | `cache.py` | 与 cache blacklist 强相关 |
| `error_handler_node` | `error_handler.py` | 独立节点，优先迁移 |
| `_compact_dpcli_snapshot`、`_render_dpcli_snapshot_text`、`_observer_dpcli_snapshot` | `observer.py` 或 `dpcli.py` | 若 executor 也复用，放 `dpcli.py` |
| `observer_node` | `observer.py` | 依赖 observer 实例、DomCache、任务连续性 |
| `rag_node`、`_rag_store_kb`、`_rag_store_cache`、`_rag_qa` | `rag.py` | 低耦合，优先迁移 |
| `_planner_completion_is_premature`、`_planner_forced_extract_plan` | `planner.py` | Planner 私有策略 |
| `planner_node` | `planner.py` | 依赖 prompt 和 common pruning |
| `_extract_json_object` | `dpcli.py` 或 `coder.py` | 当前测试直接导入，需 re-export |
| `_should_use_dpcli_action`、`_dpcli_action_context`、`_validate_dpcli_action` | `dpcli.py` | Coder/测试共用 |
| `_dpcli_action_coder_node`、`coder_node` | `coder.py` | Coder 主逻辑 |
| `_dpcli_result_url`、`_dpcli_error`、`_dpcli_failure_goto`、`_executor_dpcli_branch` | `executor.py` 或 `dpcli.py` | 若只 executor 使用，放 `executor.py` 并 re-export 测试 helper |
| `executor_node` | `executor.py` | HITL 前中断节点，迁移后重点测 |
| `verifier_node` | `verifier.py` | HITL 后中断节点，迁移后重点测 |

## 5. 分阶段实施计划

### Phase 0：准备兼容边界

目标：先让 `core.nodes` 从单文件入口变成包入口，外部调用方无感。

操作：

1. 新建临时目录 `core/nodes_pkg/`，先在里面搭好模块和 `__init__.py`
2. 把 `core/nodes.py` 的内容按模块复制过去
3. 调整 `nodes_pkg/__init__.py`，导出当前外部需要的全部符号
4. 本地跑编译和相关测试
5. 验证通过后：
   - 删除或备份迁移 `core/nodes.py`
   - 将 `core/nodes_pkg/` 重命名为 `core/nodes/`

验收：

```bash
python -m py_compile core/graph_v2.py
python -m py_compile core/nodes/*.py
python -m unittest test.test_dpcli_action_prompt test.test_dpcli_executor_node test.test_dpcli_observer_projection
```

### Phase 1：迁移低耦合节点

目标：先拆出最独立的节点，建立导入模式。

迁移顺序：

1. `error_handler.py`
2. `rag.py`
3. `common.py` 中仅迁移 RAG/ErrorHandler 需要的基础工具

风险：

- `ERROR_RECOVERY_PROMPT`、RAG config、logger 导入路径要保持清晰
- 不要在 `common.py` 中导入具体节点模块，避免循环依赖

验收：

```bash
python -m py_compile core/nodes/common.py core/nodes/error_handler.py core/nodes/rag.py
python -m py_compile core/graph_v2.py
```

### Phase 2：迁移 Cache 和 Locator 工具

目标：把 cache_lookup 及 locator dry-run 从主文件剥离。

迁移内容：

1. `locator.py`
   - locator 提取
   - locator 归一化
   - dry-run 探测
2. `verification.py`
   - cache failure 需要的 verification result 构造
3. `cache.py`
   - `cache_lookup_node`
   - `_save_code_to_cache`
   - `_save_dom_to_cache`
   - cache failure/blacklist

风险：

- Cache 节点会跳转 `Coder`、`Executor`、`Observer`，Literal 类型可以保留在节点文件内
- `_handle_cache_failure` 构造的 state update 不应改变
- `CodeCacheManager`、`vector_gateway` 等导入保持局部导入或原样迁移，避免启动时副作用扩大

验收：

```bash
python -m py_compile core/nodes/cache.py core/nodes/locator.py core/nodes/verification.py
python -m unittest discover -s test -p "test_*.py"
```

### Phase 3：迁移 Observer

目标：把感知节点和 DP-CLI snapshot 观察逻辑独立出来。

迁移内容：

1. `observer.py`
   - `observer_node`
   - `_compact_dpcli_snapshot`
   - `_render_dpcli_snapshot_text`
   - `_observer_dpcli_snapshot`
2. 如果 DP-CLI snapshot 后续会被 executor/coder 复用，再移动到 `dpcli.py`

风险：

- Observer 是入口节点，任务连续性和旧状态清理不能变
- DomCache/locator_suggestions 的写入格式不能变
- `test_dpcli_observer_projection.py` 当前导入私有 helper，`__init__.py` 必须继续导出

验收：

```bash
python -m py_compile core/nodes/observer.py
python -m unittest test.test_dpcli_observer_projection
```

### Phase 4：迁移 Planner

目标：把 planner 策略和 prompt 组装从主文件剥离。

迁移内容：

1. `planner.py`
   - `planner_node`
   - `_looks_like_global_rewrite_plan`
   - `_planner_completion_is_premature`
   - `_planner_forced_extract_plan`
2. `common.py`
   - finished_steps pruning
   - token count
   - summarizer LLM

风险：

- Planner 的 `Command(goto="__end__")` 行为不能变
- `finished_steps` 裁剪使用 `RemoveMessage`，确认 langchain import 位置正确
- Prompt token pruning 不要和业务迁移混在同一个 commit 做逻辑改动

验收：

```bash
python -m py_compile core/nodes/planner.py core/nodes/common.py
```

### Phase 5：迁移 DP-CLI、Coder、Executor

目标：把动作生成和执行路径拆清楚，同时保持测试 helper 可见。

迁移内容：

1. `dpcli.py`
   - `_extract_json_object`
   - `_should_use_dpcli_action`
   - `_dpcli_action_context`
   - `_state_has_dpcli_refs`
   - `_validate_dpcli_action`
2. `coder.py`
   - `_dpcli_action_coder_node`
   - `coder_node`
3. `executor.py`
   - `_dpcli_result_url`
   - `_dpcli_error`
   - `_dpcli_failure_goto`
   - `_executor_dpcli_branch`
   - `executor_node`

风险：

- `test_dpcli_action_prompt.py` 直接导入 `_extract_json_object`、`_validate_dpcli_action`、`coder_node`
- `test_dpcli_executor_node.py` 直接导入 `_executor_dpcli_branch`
- Executor 是 `interrupt_before` 节点，拆分后需确认 `graph_v2.py` 编译不受影响
- DP-CLI 分支的 error code 到 goto 映射不能改变

验收：

```bash
python -m py_compile core/nodes/dpcli.py core/nodes/coder.py core/nodes/executor.py
python -m unittest test.test_dpcli_action_prompt test.test_dpcli_executor_node
```

### Phase 6：迁移 Verifier

目标：最后迁移验证节点，避免前面模块尚未稳定时牵动失败恢复链路。

迁移内容：

1. `verifier.py`
   - `verifier_node`
2. `verification.py`
   - 如果 Phase 2 只迁移了部分 verification helper，此阶段补全

风险：

- Verifier 是 `interrupt_after` 节点，编译和 HITL 行为要确认
- `verification_result` 的结构必须兼容 main.py 中 HITL 相关检测
- `RAGNode`、`Planner`、`Executor` 的回跳逻辑不要变化

验收：

```bash
python -m py_compile core/nodes/verifier.py
python -m py_compile main.py core/graph_v2.py
python -m unittest discover -s test -p "test_*.py"
```

## 6. `__init__.py` 对外导出建议

`core/nodes/__init__.py` 第一阶段应保持兼容，至少导出：

```python
from core.nodes.observer import observer_node
from core.nodes.planner import planner_node
from core.nodes.cache import cache_lookup_node
from core.nodes.rag import rag_node
from core.nodes.coder import coder_node
from core.nodes.executor import executor_node
from core.nodes.verifier import verifier_node
from core.nodes.error_handler import error_handler_node

# 测试兼容导出，后续可以逐步改测试直接导入新模块
from core.nodes.dpcli import _extract_json_object, _validate_dpcli_action
from core.nodes.executor import _executor_dpcli_branch
from core.nodes.observer import (
    _compact_dpcli_snapshot,
    _render_dpcli_snapshot_text,
    _observer_dpcli_snapshot,
)
```

后续清理策略：

- 业务代码只允许导入公开节点函数
- 测试可以逐步改成从具体模块导入 helper
- 等测试改完后，再减少 `__init__.py` 中的私有 helper 导出

## 7. 循环依赖规约

拆分后必须遵守以下依赖方向：

```text
common.py        -> 不导入任何节点模块
locator.py       -> 可导入 common.py，不导入节点模块
verification.py  -> 可导入 common.py，不导入节点模块
dpcli.py         -> 可导入 common.py/verification.py，不导入 coder/executor
cache.py         -> 可导入 common.py/locator.py/verification.py
observer.py      -> 可导入 common.py/locator.py/dpcli.py
planner.py       -> 可导入 common.py
coder.py         -> 可导入 common.py/dpcli.py/locator.py
executor.py      -> 可导入 common.py/dpcli.py/verification.py/cache.py
verifier.py      -> 可导入 common.py/verification.py/cache.py
__init__.py      -> 只做 re-export
```

禁止：

- `common.py` 反向导入具体节点
- `dpcli.py` 导入 `coder.py` 或 `executor.py`
- 在模块顶层初始化浏览器、Milvus、LLM 等重资源对象

## 8. 测试与验证清单

每个阶段至少执行：

```bash
python -m py_compile core/graph_v2.py main.py
python -m py_compile core/nodes/*.py
```

DP-CLI 相关阶段执行：

```bash
python -m unittest test.test_dpcli_action_prompt
python -m unittest test.test_dpcli_executor_node
python -m unittest test.test_dpcli_observer_projection
```

最终阶段执行：

```bash
python -m unittest discover -s test -p "test_*.py"
```

如果 Milvus、浏览器、LLM 环境不完整，允许把集成测试标记为人工验证，但必须至少完成：

- Python 编译通过
- Graph 构建导入通过
- DP-CLI 单元测试通过
- `from core.nodes import ...` 兼容导入通过

建议额外检查：

```bash
python -c "from core.nodes import observer_node, planner_node, cache_lookup_node, rag_node, coder_node, executor_node, verifier_node, error_handler_node; print('ok')"
```

## 9. 回滚方案

建议按阶段提交，每个阶段只做搬迁和导入修正。

如果某阶段失败：

1. 回滚该阶段改动
2. 保留上一阶段已经通过的结构
3. 不在失败阶段混入业务修复
4. 记录失败原因，例如：
   - 循环导入
   - 私有 helper 未 re-export
   - 顶层导入触发外部服务初始化
   - 测试 monkeypatch 路径失效

## 10. 推荐提交顺序

1. `拆分 nodes 基础包入口`
2. `迁移 RAG 和错误处理节点`
3. `迁移 locator 与 cache 节点`
4. `迁移 observer 节点`
5. `迁移 planner 节点`
6. `迁移 dp_cli coder executor 节点`
7. `迁移 verifier 节点`
8. `清理 nodes 兼容导出和测试导入`

## 11. 暂不建议做的事

- 不要一次性把所有 helper 都改名为无下划线公开 API
- 不要在拆分同时修改节点路由
- 不要把每个 helper 都拆成独立文件
- 不要在拆分时改 prompt、cache 命中策略、DP-CLI action schema
- 不要删除测试里依赖的私有 helper 导出，除非同时修改测试

## 12. 最小可执行版本

如果只想先降低 `nodes.py` 压力，最小拆分可以只做四个文件：

```text
core/nodes/
  __init__.py
  common.py
  cache.py
  dpcli.py
  workflow.py
```

其中 `workflow.py` 暂时放 Observer/Planner/Coder/Executor/Verifier 等主节点，后续再细拆。这个方案风险更低，但长期可维护性不如完整目标结构。

推荐实际执行时采用完整目标结构，但以 Phase 0 到 Phase 6 分阶段落地。
