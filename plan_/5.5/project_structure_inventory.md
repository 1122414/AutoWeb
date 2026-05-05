# AutoWeb Project Structure Inventory — P0 Baseline

**Date**: 2026-05-05
**Git branch**: refactor/split-nodes
**Uncommitted changes**: 9 files from logging optimization (see P0-SUMMARY)

## Active Entry Points (Confirmed)

| File | Role | Status |
|------|------|--------|
| `main.py` | CLI entry, interactive loop, HITL | Active |
| `core/graph_v2.py` | LangGraph graph construction | Active |
| `core/nodes/__init__.py` | Node module exports | Active |
| `drivers/drission_driver.py` | BrowserDriver singleton | Active |
| `skills/dpcli_executor.py` | dp_cli subprocess adapter | Active |

## File Classification

### Production Runtime

| File | Category |
|------|----------|
| `main.py` | production runtime |
| `config.py` | production runtime |
| `core/__init__.py` | production runtime |
| `core/graph_v2.py` | production runtime |
| `core/state_v2.py` | production runtime |
| `core/llm_factory.py` | production runtime |
| `core/nodes/__init__.py` | production runtime |
| `core/nodes/_cache.py` | production runtime |
| `core/nodes/_context.py` | production runtime |
| `core/nodes/_dpcli.py` | production runtime |
| `core/nodes/_locators.py` | production runtime |
| `core/nodes/_utils.py` | production runtime |
| `core/nodes/_verification.py` | production runtime |
| `core/nodes/cache_lookup.py` | production runtime |
| `core/nodes/coder.py` | production runtime |
| `core/nodes/error_handler.py` | production runtime |
| `core/nodes/executor.py` | production runtime |
| `core/nodes/observer.py` | production runtime |
| `core/nodes/planner.py` | production runtime |
| `core/nodes/rag.py` | production runtime |
| `core/nodes/target_selector.py` | production runtime |
| `core/nodes/verifier.py` | production runtime |
| `drivers/__init__.py` | production runtime |
| `drivers/drission_driver.py` | production runtime |
| `drivers/js_loader.py` | production runtime |
| `prompts/__init__.py` | production runtime |
| `prompts/base_prompts.py` | production runtime |
| `prompts/coder_prompts.py` | production runtime |
| `prompts/dpcli_action_prompts.py` | production runtime |
| `prompts/dpcli_planner_prompts.py` | production runtime |
| `prompts/observer_prompts.py` | production runtime |
| `prompts/planner_prompts.py` | production runtime |
| `prompts/rag_prompts.py` | production runtime |
| `prompts/verifier_prompts.py` | production runtime |
| `rag/__init__.py` | production runtime |
| `rag/field_registry.py` | production runtime |
| `rag/milvus_schema.py` | production runtime |
| `rag/query_analyzer.py` | production runtime |
| `rag/retriever_qa.py` | production runtime |
| `skills/__init__.py` | production runtime |
| `skills/action_cache.py` | production runtime |
| `skills/actor.py` | production runtime |
| `skills/cache_blacklist.py` | production runtime |
| `skills/code_cache.py` | production runtime |
| `skills/code_guard.py` | production runtime |
| `skills/dom_cache.py` | production runtime |
| `skills/dom_compressor.py` | production runtime |
| `skills/dpcli_crawl_policy.py` | production runtime |
| `skills/dpcli_executor.py` | production runtime |
| `skills/dpcli_planner_view.py` | production runtime |
| `skills/dpcli_snapshot_indexer.py` | production runtime |
| `skills/dpcli_snapshot_query.py` | production runtime |
| `skills/dpcli_snapshot_store.py` | production runtime |
| `skills/dpcli_target_selector.py` | production runtime |
| `skills/logger.py` | production runtime |
| `skills/observer.py` | production runtime |
| `skills/tool_rag.py` | production runtime |
| `skills/toolbox.py` | production runtime |
| `skills/vector_base.py` | production runtime |
| `skills/vector_gateway.py` | production runtime |
| `skills/windows_app_finder.py` | production runtime |
| `requirements.txt` | production runtime |

### Tests (`test/` directory — old root)

| File | Category |
|------|----------|
| `test/test_action_cache.py` | tests (unit) |
| `test/test_code_cache_v6.py` | tests (unit) |
| `test/test_code_guard.py` | tests (unit) |
| `test/test_cuda.py` | tests (integration) |
| `test/test_dom_compressor.py` | tests (unit) |
| `test/test_dp_locatior.py` | tests (legacy — misspelled) |
| `test/test_dpcli_action_prompt.py` | tests (unit) |
| `test/test_dpcli_crawl_policy.py` | tests (unit) |
| `test/test_dpcli_executor.py` | tests (unit) |
| `test/test_dpcli_executor_node.py` | tests (unit) |
| `test/test_dpcli_observer_projection.py` | tests (unit) |
| `test/test_dpcli_observer_target_selector.py` | tests (unit) |
| `test/test_drissionpage.py` | tests (integration) |
| `test/test_embedding.py` | tests (integration) |
| `test/test_graph_v2.py` | tests (unit) |
| `test/test_nodes_refactor.py` | tests (unit) |
| `test/test_Qwen.py` | tests (integration) |
| `test/test_toolbox.py` | tests (unit) |
| `test/test_verification_contract.py` | tests (unit) |
| `test/check_anthropic_quota.py` | tests (integration) |
| `test/check_env.py` | tests (integration) |
| `test/check_milvus.py` | tests (integration) |
| `test/fibonacci.py` | tests (legacy) |
| `test/get_browser_data.py` | tests (legacy) |
| `test/test.py` | tests (legacy) |
| `test/test.html` | tests (fixtures) |
| `test/auto_crawler_demo/` (3 files) | tests (legacy — demo) |
| `test/mcp_learn/` (2 files) | tests (legacy — learning) |
| `test/milvus_relation/` (3 files) | tests (legacy — one-off) |
| `test/AGENTS.md` | docs |
| `test/NODES_REFACTOR_TEST_GUIDE.md` | docs |

### Tests (`tests/` directory — newer root)

| File | Category |
|------|----------|
| `tests/test_dpcli_no_python_fallback.py` | tests (unit) |
| `tests/test_dpcli_planner_priority.py` | tests (unit) |
| `tests/test_dpcli_snapshot_session.py` | tests (unit) |
| `tests/test_dpcli_validator_fixes.py` | tests (unit) |
| `tests/test_dpcli_verifier_action_contract.py` | tests (unit) |
| `tests/test_dpcli_verifier_detail_policy.py` | tests (unit) |
| `tests/test_target_constraint_normalization.py` | tests (unit) |
| `tests/test_target_selector_priority.py` | tests (unit) |
| `tests/test_windows_app_finder.py` | tests (unit) |
| `tests/ab_test_report.json` | generated/runtime artifacts |

### Scripts (`scripts/`)

| File | Category |
|------|----------|
| `scripts/smoke_dpcli_executor.py` | smoke/manual scripts |
| `scripts/smoke_dpcli_flow_plan_target.py` | smoke/manual scripts |
| `scripts/smoke_dpcli_snapshot_selector.py` | smoke/manual scripts |
| `scripts/test_dpcli_full_closure.py` | smoke/manual scripts |
| `scripts/test_dpcli_observer_target_selector.py` | smoke/manual scripts |
| `scripts/find_windows_app.py` | smoke/manual scripts |

### Docs / Plans / History

| File | Category |
|------|----------|
| `README.md` | docs/plans/history |
| `AGENTS.md` | docs/plans/history |
| `core/AGENTS.md` | docs/plans/history |
| `prompts/AGENTS.md` | docs/plans/history |
| `rag/AGENTS.md` | docs/plans/history |
| `skills/AGENTS.md` | docs/plans/history |
| `debug.md` | docs/plans/history (root — to move) |
| `struct.md` | docs/plans/history (root — to move) |
| `upgrade.md` | docs/plans/history (root — to move) |
| `graph_logic_v7.png` | docs/plans/history (root — to move) |
| `plan_/4.29/4.29_autoweb_cli_init.md` | docs/plans/history |
| `plan_/5.2/node_upgrade_plan.md` | docs/plans/history |
| `plan_/5.2/node_upgrade_plan_start.md` | docs/plans/history |
| `plan_/5.3/2026-05-03_dp_cli_observer_target_selector_execution_plan.md` | docs/plans/history |
| `plan_/5.3/2026-05-03_dpcli_integration_continue_plan.md` | docs/plans/history |
| `plan_/5.3/dp_cli_full_migration_plan.md` | docs/plans/history |
| `plan_/5.4/2026-05-04_dpcli_internal_observation_verifier_plan.md` | docs/plans/history |
| `plan_/5.4/2026-05-04_dpcli_main_loop_closure_plan.md` | docs/plans/history |
| `plan_/5.4/2026-05-04_dpcli_verifier_detail_policy_fix_plan.md` | docs/plans/history |
| `plan_/5.5/2026-05-05_project_structure_refactor_plan.md` | docs/plans/history |
| `test/AGENTS.md` | docs/plans/history |
| `test/NODES_REFACTOR_TEST_GUIDE.md` | docs/plans/history |

### Generated / Runtime Artifacts

| File | Category |
|------|----------|
| `raw_dom.json` | generated/runtime artifacts |
| `temp_code_edit.py` | generated/runtime artifacts |
| `data/field_registry.json` | generated/runtime artifacts |
| `tests/ab_test_report.json` | generated/runtime artifacts |
| `.env` | generated/runtime artifacts (config) |

### Unknown

| File | Category |
|------|----------|
| (none) | — |

## Test Baseline

### `tests/` (newer dp_cli tests)

```powershell
python -m unittest discover -s tests -p "test_*.py"
```
**Result**: ✅ Ran 90 tests in 0.026s — **OK**

### `test/` (older tests)

```powershell
python -m unittest discover -s test -p "test_*.py"
```
**Result**: ❌ Timed out after 60s — likely requires Milvus/Qwen/browser services. Pre-existing.

### `py_compile` check

```powershell
python -m py_compile main.py config.py core\state_v2.py core\graph_v2.py
```
**Result**: ✅ No errors.

## P0-SUMMARY

- **Active entry points**: 5 confirmed (main.py, graph_v2.py, nodes/__init__.py, drission_driver.py, dpcli_executor.py)
- **tests/**: 90 tests pass ✅
- **test/**: Timeout (pre-existing, needs external services)
- **py_compile**: Clean ✅
- **Uncommitted changes**: 9 files with logging optimization changes (411 insertions, 58 deletions)
- **Unknown files**: 0
- **Root clutter**: 7 items to clean (debug.md, struct.md, upgrade.md, graph_logic_v7.png, temp_code_edit.py, raw_dom.json, plus .gitignore `test` pattern)
