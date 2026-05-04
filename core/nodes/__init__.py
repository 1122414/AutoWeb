"""AutoWeb LangGraph 节点包

将 core/nodes.py 拆分为按职责域组织的模块包，保持外部导入兼容。

拆分原则:
- 外部导入路径保持不变: from core.nodes import observer_node, planner_node, ...
- 不引入 add_conditional_edges，继续由节点返回 Command(goto="...")
- 先迁移低耦合模块，再迁移高耦合模块
- 拆分过程只做搬迁，不修改业务逻辑

目录结构:
    core/nodes/
        __init__.py          # 统一对外导出，兼容原 core.nodes 导入
        _utils.py            # 通用 helper：config/browser/time/url/token
        _locators.py         # locator 提取、归一化、dry-run
        _verification.py     # verification 结果构造、解析、失败判断
        _cache.py            # cache 保存/失败处理
        _context.py          # 上下文裁剪：locator_suggestions / finished_steps
        _dpcli.py            # DP-CLI action/snapshot 共用工具
        observer.py          # observer_node + DP-CLI snapshot projection
        planner.py           # planner_node + planner completion/forced plan helper
        cache_lookup.py      # cache_lookup_node
        rag.py               # rag_node + RAG 子函数
        coder.py             # coder_node + DP-CLI action coder branch
        executor.py          # executor_node + DP-CLI executor branch
        verifier.py          # verifier_node
        error_handler.py     # error_handler_node

依赖方向（禁止反向依赖）:
    _utils.py        -> 不导入任何节点模块
    _locators.py     -> 可导入 _utils.py，不导入节点模块
    _verification.py -> 可导入 _utils.py，不导入节点模块
    _context.py      -> 可导入 _utils.py，不导入节点模块
    _cache.py        -> 可导入 _utils.py/_locators.py/_verification.py
    _dpcli.py        -> 可导入 _utils.py/_verification.py，不导入 coder/executor
    observer.py      -> 可导入 _utils.py/_locators.py/_dpcli.py/_cache.py
    planner.py       -> 可导入 _utils.py/_context.py/_verification.py
    cache_lookup.py  -> 可导入 _utils.py/_locators.py/_cache.py
    rag.py           -> 可导入 _utils.py/_cache.py
    coder.py         -> 可导入 _utils.py/_dpcli.py/_locators.py
    executor.py      -> 可导入 _utils.py/_dpcli.py/_verification.py/_cache.py
    verifier.py      -> 可导入 _utils.py/_verification.py/_cache.py/_context.py
    error_handler.py -> 可导入 _utils.py/_verification.py
    __init__.py      -> 只做 re-export
"""

from __future__ import annotations

# ============================================================================
# 第一阶段：基础工具模块（被所有节点依赖）
# ============================================================================
from core.nodes._utils import (
    _get_tab,
    _parse_iso_datetime,
    _is_hit_from_current_task,
    _detect_task_continuity,
    _count_tokens,
    _get_summarizer_llm,
)

from core.nodes._locators import (
    _extract_locator_info,
    _extract_domain_key_from_url,
    _build_step_context,
    _extract_locator_candidates,
    _extract_locators_from_strategies,
    _normalize_locator_token,
    _has_locator_overlap,
    _sanitize_locator,
    _is_valid_element,
    _probe_locator,
    _dry_run_observer_strategies,
    _dry_run_cache_hit_locators,
    _normalize_strategy_list,
)

from core.nodes._verification import (
    _normalize_failure_scope,
    _normalize_verification_source,
    _build_verification_result,
    _coerce_verification_result,
    _is_failed_verification,
    _parse_verifier_result_content,
    _verification_focus_text,
)

from core.nodes._context import (
    _prune_locator_suggestions,
    _prune_finished_steps,
)

from core.nodes._cache import (
    _save_code_to_cache,
    _save_dom_to_cache,
    _record_cache_failure,
    _handle_cache_failure,
)

from core.nodes._dpcli import (
    _extract_json_object,
    _should_use_dpcli_action,
    _dpcli_action_context,
    _state_has_dpcli_refs,
    _validate_dpcli_action,
    _compact_dpcli_snapshot,
    _render_dpcli_snapshot_text,
    _observer_dpcli_snapshot,
    _dpcli_result_url,
    _dpcli_error,
    _dpcli_failure_goto,
    _dpcli_planner_context,
    _dpcli_action_kind,
    _compact_result_evidence,
)

# ============================================================================
# 第二阶段：节点函数（公开 API）
# ============================================================================
from core.nodes.observer import observer_node
from core.nodes.planner import planner_node, _looks_like_global_rewrite_plan, _planner_completion_is_premature, _planner_forced_extract_plan
from core.nodes.cache_lookup import cache_lookup_node
from core.nodes.rag import rag_node
from core.nodes.coder import coder_node
from core.nodes.executor import executor_node
from core.nodes.verifier import verifier_node
from core.nodes.error_handler import error_handler_node
from core.nodes.target_selector import target_selector_node

# ============================================================================
# 第三阶段：测试兼容导出（后续可逐步迁移测试直接导入新模块）
# ============================================================================
from core.nodes.coder import _executor_dpcli_branch

__all__ = [
    # 公开节点函数
    "observer_node",
    "planner_node",
    "cache_lookup_node",
    "rag_node",
    "coder_node",
    "executor_node",
    "verifier_node",
    "error_handler_node",
    "target_selector_node",
    # 测试兼容导出的私有 helper
    "_extract_json_object",
    "_validate_dpcli_action",
    "_executor_dpcli_branch",
    "_compact_dpcli_snapshot",
    "_render_dpcli_snapshot_text",
    "_observer_dpcli_snapshot",
    "_build_verification_result",
]
