# Prompt 一致性修复报告 (Consistency Fix Report)

针对 "Coder 瞎编"、"Planner 瞎指挥"、"Observer 不完整" 的三角死结，已实施以下修复：

## 1. Observer (观察者) - 拒绝残缺
- **位置**: `output_format_prompt.py` (Universal Extraction)
- **新规**: 增加了 `Completeness` (完整性优先) 铁律。
- **效果**: 必须为每一个需求字段找到定位符，严禁遗漏。如果详情页才有，必须显式标记 `Need Detail Page`，而不是让 Coder 去猜。

## 2. Coder (代码员) - 拒绝幻觉
- **位置**: `action_prompts.py` (Code Gen)
- **新规**: 增加了 **Rule 7: Anti-Hallucination (反幻觉)**。
- **效果**: 严禁凭空臆造 XPath。代码必须严格基于 Planner/Observer 提供的 `strategy` 字典。如果字典里没字段，Coders 必须打印 Warning 并跳过，绝不允许自己 `x://div[999]`瞎写。

## 3. Planner (规划师) - 拒绝越权
- **位置**: `planner_prompts.py` (Protocol)
- **新规**: 增加了 **Separation of Concerns (职责分离)**。
- **效果**: Planner 禁止在自然语言计划中包含具体的 CSS/XPath 选择器 (如 `click a.btn`)。Planner 只负责 "What" (点击搜索按钮)，Observer 负责 "Where" (.btn)。防止 Planner 的劣质定位误导 Coder。

通过这三层加固，Agent 的执行链路应该会从 "猜疑链" 变成 "信任链"。请重新尝试任务。
