# Token Consumption Analysis & Optimization Report

## 1. Token Consumption Ranking (Per Step)

Assuming a typical webpage DOM size of 30k tokens (compressed).

| Rank | Component | Usage Pattern | Estimated Tokens | Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **1** | **Observer (in Coder)** | `analyze_locator_strategy(dom)` | ~30k - 50k | **REDUNDANT** (Duplicate of Planner's insight) |
| **2** | **Observer (in Planner)** | `analyze_locator_strategy(dom)` | ~30k - 50k | **Essential** (For accurate planning) |
| **3** | **Planner Node** | `dom` + `suggestions` + `history` | ~35k | **Essential** (Big context needed for decision) |
| **4** | **Verifier Node** | `dom` (truncated 15k) + `log` | ~15k - 20k | **Necessary** (But can be further optimized) |
| **5** | **Coder Node** | `xpath_plan` + `history` | ~2k - 5k | **Low** (If relying on Observer's output) |

**Total Impact**: currently, a single step (e.g., clicking 1 button) might consume **100k+ tokens** due to the double DOM injection in Observer calls and Planner.

## 2. Issues Identified

### A. The "Double Vision" Problem (Major Waste)
- **Planner** calls `observer.analyze_locator_strategy` to generate "Visual Suggestions" to help it plan.
- **Coder** calls `observer.analyze_locator_strategy` *AGAIN* to generate "XPath Plan" to help it code.
- **Result**: We pay for the massive DOM processing twice.

### B. Massive DOM Context
- Both Planner and Observer are injecting 30k-50k characters of DOM. If the webpage is complex (like Taobao or YouTube), this blows up the context window.

## 3. Recommended Solutions

### Solution 1: Share Perception (Pass-through)
- **Action**: Store the results of Planner's `analyze_locator_strategy` into `AgentState`.
- **Coder**: Read the *cached* suggestions from `State` instead of calling `analyze_locator_strategy` again.
- **Savings**: **Eliminates Rank #1 entirely**, saving ~30-40% of total cost.

### Solution 2: Coder "Blind" Mode (Trust Planner)
- **Action**: Since we enforce "Atomic Steps" and "Strict Alignment", the Planner's output ("Click #btn-login") is already very specific.
- **Coder**: Often doesn't need to re-analyze the DOM if the Planner provides a clear selector.
- **Savings**: Further reduces dependency on heavy visual tasks.

### Solution 3: Smart Truncation
- **Action**: `js_loader.py` already prunes the DOM. We can be more aggressive for "Intermediate" steps and only do full scan for "Extraction" steps.

## 4. Next Steps
1. Modify `core/state.py`: Add `locator_suggestions` to `AgentState`.
2. Modify `core/planner.py`: Save analysis results to State.
3. Modify `core/graph.py` (Coder): Use cached suggestions instead of re-running analysis.
