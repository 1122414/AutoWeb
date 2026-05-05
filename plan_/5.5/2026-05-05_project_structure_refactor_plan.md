# 2026-05-05 AutoWeb Project Structure Refactor Plan

## Goal

Clean up AutoWeb's project structure without changing product behavior.

The current repository is usable, but it has become hard to reason about because production code, migration plans, smoke scripts, runtime artifacts, legacy tests, debug files, and dp_cli transition code all live close together. This plan is for opencode to execute in small, reviewable rounds.

Primary outcomes:

- Make the repository root boring and readable.
- Separate production code, tests, docs, scripts, runtime artifacts, and historical migration material.
- Keep the LangGraph + dp_cli runtime behavior intact.
- Delete or archive files only after reference checks.
- Unify test layout enough that one documented test command works.
- Reduce accidental complexity before touching deeper architecture.

Non-goals:

- Do not redesign LangGraph flow.
- Do not replace DrissionPage or dp_cli.
- Do not rewrite RAG, Milvus, cache, or browser driver internals.
- Do not change public config names, CLI behavior, or `.env` semantics unless explicitly called out.
- Do not do broad rename churn unless the step has a narrow rollback path.

## Current Facts

Observed structure:

```text
AutoWeb/
  main.py
  config.py
  core/
  skills/
  prompts/
  rag/
  drivers/
  scripts/
  test/
  tests/
  plan_/
  logs/
  output/
  browser_data/
  data/
  debug.md
  struct.md
  upgrade.md
  graph_logic_v7.png
  raw_dom.json
  temp_code_edit.py
```

Key issues:

- `test/` and `tests/` both exist. `test/` contains real tests, old experiments, local checks, HTML fixtures, and AGENTS.md. `tests/` contains newer dp_cli unit tests plus `ab_test_report.json`.
- `.gitignore` currently ignores `test`, while tracked test files still exist. This is confusing and can hide new test files from `git status`.
- Runtime and generated artifacts exist locally: `logs/`, `output/`, `browser_data/`, `raw_dom.json`, `.pytest_cache/`, `__pycache__/`.
- Root contains historical or temporary files: `debug.md`, `struct.md`, `upgrade.md`, `graph_logic_v7.png`, `temp_code_edit.py`.
- `scripts/` mixes useful smoke scripts with files named `test_*`, which look like test files but are not part of normal unittest discovery.
- `core/nodes/` is already split, but some files remain large and mixed-purpose:
  - `core/nodes/_dpcli.py`
  - `core/nodes/observer.py`
  - `core/nodes/planner.py`
  - `core/nodes/executor.py`
  - `core/nodes/verifier.py`
- `skills/` mixes stable runtime modules, dp_cli modules, cache/vector modules, and utility experiments.
- `plan_` contains useful history, but it is noisy when treated like active implementation guidance.

## Execution Rules For opencode

Follow these rules for every phase:

1. Start with `git status --short`.
2. Do not revert user changes.
3. Before moving or deleting any file, run reference checks:
   - `rg "filename_or_symbol"`
   - `rg "import_path_or_module_name"`
   - check README, scripts, tests, and dynamic imports.
4. Prefer archive/move over deletion for historical material unless a file is clearly generated or temporary.
5. Keep each phase as a separate commit if the user asks for commits.
6. After every phase, run the verification listed for that phase.
7. If a verification was already failing before the phase, record it as pre-existing.
8. Do not fix unrelated runtime bugs during structure cleanup. Create notes instead.

Recommended baseline before any edits:

```powershell
python -m py_compile main.py config.py core\state_v2.py core\graph_v2.py
python -m unittest discover -s tests -p "test_*.py"
python -m unittest discover -s test -p "test_*.py"
```

If imports fail because optional dependencies are missing, record the missing dependency and continue with narrower tests that already have stubs.

## Target Shape

Aim for this final layout:

```text
AutoWeb/
  main.py
  config.py
  requirements.txt
  README.md
  AGENTS.md
  core/
    graph_v2.py
    state_v2.py
    llm_factory.py
    nodes/
  skills/
    browser/
    cache/
    dpcli/
    rag_tools/
    safety/
    system/
  prompts/
  rag/
  drivers/
  scripts/
    smoke/
    maintenance/
  tests/
    unit/
    integration/
    fixtures/
    legacy/
  docs/
    architecture/
    debugging/
    migration/
  plan_/
    5.5/
  runtime/
    README.md
```

Important: this is the target direction, not a single massive move. Prefer reaching it over several small PRs.

## P0 - Baseline And Inventory

Purpose: establish what is active, what is historical, and what is generated.

Tasks:

1. Capture current tree:

   ```powershell
   rg --files | sort > output\project_file_inventory_before.txt
   ```

   If `output/` is ignored, this is fine as a local artifact.

2. Build a file classification table in `plan_/5.5/project_structure_inventory.md` with these categories:

   - production runtime
   - tests
   - smoke/manual scripts
   - docs/plans/history
   - generated/runtime artifacts
   - unknown

3. Confirm active entry points:

   - `main.py`
   - `core/graph_v2.py`
   - `core/nodes/__init__.py`
   - `drivers/drission_driver.py`
   - `skills/dpcli_executor.py`

4. Confirm test commands that currently pass or fail:

   ```powershell
   python -m unittest discover -s tests -p "test_*.py"
   python -m unittest discover -s test -p "test_*.py"
   ```

5. Record failures without fixing them unless the failure blocks structural cleanup.

Acceptance:

- Inventory document exists.
- Baseline commands and results are recorded.
- Unknown files are listed explicitly, not guessed.

## P1 - Root Directory Cleanup

Purpose: make the repo root show only important project entry points.

Candidate moves:

```text
debug.md        -> docs/debugging/debug.md
struct.md       -> docs/architecture/struct.md
upgrade.md      -> docs/migration/upgrade.md
graph_logic_v7.png -> docs/architecture/graph_logic_v7.png
```

Candidate deletion:

```text
temp_code_edit.py
raw_dom.json
```

Rules:

- Delete `temp_code_edit.py` only if `rg "temp_code_edit"` has no meaningful references.
- Delete `raw_dom.json` only if it is generated local state and not used by tests or docs.
- If unsure, move questionable files to `docs/archive/` instead of deleting.

Also update `.gitignore`:

- Keep ignoring runtime artifacts:
  - `logs/`
  - `output/`
  - `browser_data/`
  - `.pytest_cache/`
  - `__pycache__/`
  - `*.pyc`
  - `.env`
  - `*.log`
- Stop ignoring the whole `test` directory. It currently contains tracked tests and should not hide new test files.
- Add explicit ignores for local/generated fixtures only if needed.

Acceptance:

- Root no longer contains temporary/debug/history files except README, AGENTS, config, main, requirements.
- `git status --short` does not hide new tests.
- Existing tests still import.

Verification:

```powershell
python -m py_compile main.py config.py
python -m unittest discover -s tests -p "test_*.py"
```

## P2 - Test Directory Consolidation

Purpose: make test discovery predictable.

Target:

```text
tests/
  unit/
  integration/
  fixtures/
  legacy/
```

Suggested classification:

- Move newer `tests/test_*.py` into `tests/unit/`.
- Move stable unit tests from `test/test_*.py` into `tests/unit/`.
- Move browser/live environment tests into `tests/integration/`:
  - `test_drissionpage.py`
  - `test_embedding.py`
  - `test_Qwen.py`
  - `test_cuda.py`
  - `check_milvus.py`
  - `check_env.py`
  - `check_anthropic_quota.py`
- Move large fixtures into `tests/fixtures/`:
  - `test.html`
- Move questionable experiments into `tests/legacy/`:
  - `fibonacci.py`
  - `test.py`
  - `get_browser_data.py`
  - misspelled or unclear files until reviewed, such as `test_dp_locatior.py`.

Important dependency note:

- `test/test_dpcli_action_prompt.py` imports `test_dpcli_executor_node` for lightweight stubs. If these files move, update imports so stubs still resolve. Prefer creating a small shared stub helper under `tests/unit/stubs.py` rather than relying on cross-test import side effects.

Commands after consolidation:

```powershell
python -m unittest discover -s tests\unit -p "test_*.py"
python -m unittest discover -s tests\integration -p "test_*.py"
```

Integration tests may require external services or browser sessions. Mark them separately in README.

Acceptance:

- Only one primary test root: `tests/`.
- Unit tests do not depend on live Milvus, CUDA, Qwen, DrissionPage browser state, or external credentials.
- Integration tests are clearly separated.
- README has one unit test command and one integration test command.

Verification:

```powershell
python -m unittest discover -s tests\unit -p "test_*.py"
```

## P3 - Scripts Cleanup

Purpose: separate manual smoke scripts from tests.

Target:

```text
scripts/
  smoke/
    smoke_dpcli_executor.py
    smoke_dpcli_flow_plan_target.py
    smoke_dpcli_snapshot_selector.py
    test_dpcli_full_closure.py
    test_dpcli_observer_target_selector.py
  maintenance/
    find_windows_app.py
```

Optional rename:

- Rename script files beginning with `test_` to `smoke_...` only if imports and docs are updated in the same commit.
- If rename risk is high, keep names but move them under `scripts/smoke/`.

Update references in:

- README
- `plan_`
- any test imports
- any manual docs

Acceptance:

- `scripts/` no longer looks like a second test root.
- Smoke scripts remain manually runnable.

Verification:

```powershell
python scripts\smoke\smoke_dpcli_executor.py
```

If this requires a live browser/session and fails, record that it is environment-dependent.

## P4 - Documentation And Plan Archive

Purpose: make active guidance easy to find.

Create:

```text
docs/
  architecture/
  debugging/
  migration/
  archive/
```

Move historical root docs as described in P1.

Keep `plan_` as chronological implementation history, but add:

```text
plan_/README.md
```

Content should explain:

- plans are historical task plans
- newest active plan is usually highest date/version
- not all old plans describe current behavior

Update README with:

- actual current structure
- active entry points
- test commands
- runtime directories
- warning that `logs/`, `output/`, `browser_data/`, `data/` are local/runtime

Acceptance:

- README tells a new contributor where to start.
- Historical plans are not mistaken for source of truth.

Verification:

```powershell
rg "debug.md|struct.md|upgrade.md|graph_logic_v7.png"
```

All references should point to the new paths or be intentionally historical.

## P5 - Module Boundary Cleanup

Purpose: improve code organization without broad behavior changes.

Do this only after P0-P4.

Suggested `skills/` target grouping:

```text
skills/
  dpcli/
    executor.py
    crawl_policy.py
    planner_view.py
    snapshot_indexer.py
    snapshot_query.py
    snapshot_store.py
    target_selector.py
  cache/
    action_cache.py
    cache_blacklist.py
    code_cache.py
    dom_cache.py
  browser/
    actor.py
    observer.py
    dom_compressor.py
  rag_tools/
    tool_rag.py
    vector_base.py
    vector_gateway.py
  safety/
    code_guard.py
  system/
    logger.py
    toolbox.py
    windows_app_finder.py
```

Risk warning:

- This phase touches import paths across the repo.
- Do not combine this with behavior edits.
- Consider compatibility shims for one release:

  ```python
  # skills/dpcli_executor.py
  from skills.dpcli.executor import *
  ```

  Remove shims in a later cleanup only after all imports are migrated.

Acceptance:

- No production behavior changes.
- Import paths are consistent.
- Compatibility shims are documented if used.

Verification:

```powershell
python -m py_compile main.py config.py core\graph_v2.py core\state_v2.py
python -m unittest discover -s tests\unit -p "test_*.py"
```

## P6 - Core Node Simplification

Purpose: reduce the largest mixed-purpose files after structure is clearer.

Candidates:

- `core/nodes/_dpcli.py`
- `core/nodes/observer.py`
- `core/nodes/planner.py`
- `core/nodes/executor.py`
- `core/nodes/verifier.py`

Do not split everything at once.

Recommended order:

1. Extract pure dp_cli helpers out of `core/nodes/_dpcli.py` into a dedicated runtime module under the dp_cli area.
2. Keep LangGraph node wrappers in `core/nodes/`.
3. Move reusable pure functions to modules that can be tested without LangGraph stubs.
4. Add tests around extracted pure functions before changing call sites.

Desired boundary:

```text
core/nodes/
  observer.py          # LangGraph node wrapper
  planner.py           # LangGraph node wrapper
  coder.py             # LangGraph node wrapper
  executor.py          # LangGraph node wrapper
  verifier.py          # LangGraph node wrapper
  target_selector.py   # LangGraph node wrapper

skills/dpcli/
  planning.py          # pure planner rewrite/context helpers
  actions.py           # validation/classification/action conversion
  snapshots.py         # snapshot compacting/store/query coordination
```

Acceptance:

- `core/nodes/*` files become mostly orchestration.
- Pure logic can be imported in unit tests without full LangGraph/browser dependencies.
- No node names or graph routing behavior changes.

Verification:

```powershell
python -m unittest discover -s tests\unit -p "test_*.py"
```

## P7 - Dead Code And Dependency Pruning

Purpose: delete confirmed unused code after layout is stable.

Candidates to investigate:

- old DOM cache paths if dp_cli is now primary
- old Python-code execution fallback if product direction is dp_cli-only
- unused prompt templates
- unused vector/RAG helpers
- unused scripts
- stale config keys
- unused dependencies in `requirements.txt`

Hard rule:

- Do not delete legacy fallback code just because it is ugly. Delete only after the user confirms the product no longer supports that mode, or after tests prove it is unreachable and docs agree.

Reference checks:

```powershell
rg "SYMBOL_OR_CONFIG_KEY"
rg "module_name"
rg "ENV_VAR_NAME"
```

Acceptance:

- Every deletion has a note explaining why it is safe.
- No tests are deleted just to make cleanup pass.
- README no longer documents removed paths.

Verification:

```powershell
python -m py_compile main.py config.py core\graph_v2.py core\state_v2.py
python -m unittest discover -s tests\unit -p "test_*.py"
```

## Proposed Commit Sequence

If opencode commits each phase, use small commit themes:

1. `整理项目结构基线清单`
2. `清理仓库根目录文档和临时文件`
3. `统一测试目录结构`
4. `整理冒烟脚本目录`
5. `补充项目结构文档`
6. `收敛skills模块边界`
7. `拆分dp_cli纯逻辑`
8. `清理确认无用代码`

## Stop Conditions

Stop and ask the user before continuing if:

- A moved file is imported dynamically and the reference path is unclear.
- Unit tests become newly broken.
- A cleanup step requires changing runtime behavior.
- A deletion candidate may be part of a user workflow.
- Optional service tests fail and it is unclear whether failure is environmental.

## Done Definition

The refactor is done when:

- New contributors can understand the repo from README and directory names.
- There is one primary test root.
- Runtime artifacts are ignored and not mixed with source.
- Historical docs are archived.
- Smoke scripts are separate from tests.
- Core runtime imports still work.
- Unit tests have a documented command that passes.
- Any remaining messy areas are listed as known debt rather than silently mixed into the main structure.
