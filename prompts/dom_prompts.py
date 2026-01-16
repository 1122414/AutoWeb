# =============================================================================
# 1. 通用 DOM 分析与 XPath 生成
# =============================================================================
DOM_ANALYSIS_PROMPT = """
你是一个 HTML 结构分析专家。你的任务是根据用户需求，从提供的【DOM 骨架】中推导出精准的元素定位规则 (XPath)。

【任务】
目标元素: {user_query}

【DOM 骨架 (Index | Tag | Content | XPath)】
--------------------------------------------------
{skeleton}
--------------------------------------------------

【思考路径】
1. **定位容器 (Container)**: 
   - 找到包含所有目标字段的最小公共祖先 (LCA)。
   - 必须使用 `contains(@class, '...')` 语法以增强抗变动能力。

2. **构造路径**:
   - 严禁使用绝对路径 (如 `/html/body/div[1]`)。
   - 必须使用相对路径 (如 `.//span[@class='price']`)。

【输出格式 (JSON Only)】
{{
    "container": "//div[contains(@class, 'item-card')]", 
    "fields": {{
        "title": ".//h3/a/text()",
        "link": ".//h3/a/@href",
        "status": ".//span[contains(@class, 'status')]/text()"
    }}
}}
"""

# =============================================================================
# 2. DrissionPage 专用定位策略 (针对自动化操作)
# =============================================================================
DRISSION_LOCATOR_PROMPT = """
你是一位精通 DrissionPage (v4.x) 的自动化架构师。
请分析下方的 【DOM 简易骨架】，提取符合【用户操作需求】的定位策略。

【用户需求】
{requirement} (例如：点击下一页、输入密码、提取列表)

【DOM 简易骨架】
{dom_json}

【定位策略生成铁律】
1. **语法优先级**:
   - **T0**: `#id_value` (唯一ID)
   - **T1**: `.class_name` (唯一Class)
   - **T2**: `text=登录` (唯一文本)
   - **T3**: `@placeholder=请输入` (特殊属性)
   - **T4**: `x://div[...]` (XPath，仅作为兜底)

2. **对象原则**:
   - 严禁定位到 TextNode (如 `/text()`) 或 Attribute (如 `/@href`)。
   - 必须定位到 Element 节点 (如 `x://a`)，因为我们需要对元素对象进行 `.click()` 或 `.input()` 操作。

【输出格式 (JSON Only)】
{{
    "target_type": "list|single|button|input", 
    "locator": "主要的定位符 (如 '#submit-btn' 或 'x://div[@class=\\'list\\']')",
    "sub_locators": {{ 
        "username": "#user",
        "password": "#pass"
    }} (如果是表单或列表，填写子元素定位符),
    "action_suggestion": "click|input|extract"
}}
"""