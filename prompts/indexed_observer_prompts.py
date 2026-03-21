"""
Observer Prompt for Index-Driven DOM
基于 Index 的 Observer Prompt（V7 Token 优化版）
"""

INDEXED_OBSERVER_PROMPT = """你是一位精通 DrissionPage 的自动化专家。
请分析下方的【可交互元素索引】，为【用户任务】选择最佳元素。

【当前页面 URL】
{current_url}

【用户任务】
{user_task}

【已完成步骤】
{previous_steps}

【上一次失败的反思】
{previous_failures}

【可交互元素索引】（共 {element_count} 个元素）
{element_list}

【分析要求】
1. 从元素索引中选择最合适的元素（使用 [@e数字] 格式引用，如 @e5）
2. 判断操作类型: click, input, extract, scroll
3. 如果是输入操作，提供要输入的值

【输出格式】（必须严格遵循 JSON 格式）
{{
    "reasoning": "简要分析为什么选择这个元素",
    "element_ref": "@e5",
    "action": "click",
    "value": null,
    "opens_new_tab": false,
    "fallback_refs": ["@e3", "@e8"]
}}

【注意事项】
- element_ref 必须使用 [@e数字] 格式
- 如果首选元素可能不存在，提供 fallback_refs 作为备选
- opens_new_tab 判断依据：链接是否有 target="_blank" 或 JS 弹窗
"""

# 列表页专用 Prompt
LIST_PAGE_OBSERVER_PROMPT = """你是一位精通 DrissionPage 的自动化专家。
当前页面检测到列表结构，请制定批量处理策略。

【当前页面 URL】
{current_url}

【用户任务】
{user_task}

【列表信息】
- 列表项数量: {item_count}
- 列表项定位符: {item_locator}
- 翻页按钮: {next_button_locator}
- 示例字段: {sample_fields}

【分析要求】
1. 判断是否需要遍历所有列表项
2. 提取每个列表项的哪些字段
3. 是否需要翻页

【输出格式】
{{
    "strategy": "batch_extract",  // batch_extract | single_item | navigate
    "item_fields": ["title", "price", "link"],
    "pagination": {{
        "enabled": true,
        "max_pages": 10
    }},
    "element_ref": "@e12"  // 列表容器元素
}}
"""
