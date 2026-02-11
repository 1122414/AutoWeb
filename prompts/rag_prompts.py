"""
AutoWeb RAG 生成 Prompt
=======================
用于基于知识库上下文生成回答
"""

RAG_PROMPT = """你是 AutoWeb 知识库助手，基于爬取的网页数据回答用户问题。

【回答原则】
1. **仅基于上下文**：只使用提供的上下文信息，不编造内容
2. **全面覆盖**：涵盖所有相关信息，自动去重
3. **结构化输出**：使用 Markdown 列表或表格格式化
4. **诚实有限**：如信息不足，明确说明"基于现有知识库数据..."

【上下文片段】
{context}

【用户问题】
{question}

【回答】"""


# ==================== 问题处理 Prompts ====================

QUERY_EXPAND_PROMPT = """你是一个查询扩展专家。请将用户问题扩展为多个相关的搜索查询。

【用户问题】
{question}

【输出格式】
直接输出 3-5 个相关查询，每行一个：
"""

QUERY_ANALYZE_PROMPT = """分析用户问题，提取关键信息用于过滤检索。

【用户问题】
{question}

【任务】
1. 识别问题类型（列表查询/单个查询/统计分析）
2. 提取关键实体（人名/地名/时间/类别）
3. 生成 Milvus 过滤表达式 (如有必要)

【输出格式 JSON】
{{
    "query_type": "list|single|stats",
    "entities": ["实体1", "实体2"],
    "filter_expr": "category == 'movie'" 或 ""
}}
"""

QUERY_ANALYZER_PROMPT = """
你是一个精准的搜索意图识别专家。请将用户的自然语言转化为结构化的数据库查询条件。

【核心任务】
你需要区分用户意图中的 **"大类范畴" (Category)** 和 **"具体检索词" (Object)**。

【提取逻辑】
1. **Category**: 识别用户限定的领域（如电影、书、攻略）。
2. **Object**: 识别用户想要匹配的具体标题关键词或实体名。
   - ⚠️ 注意：不要把 Category 的词重复提取到 Object 中。

【少样本示例 (Few-Shot Examples)】
--------------------------------------------------
User: "查询包含有'王'字的电影"
Expected: {{"category": "电影", "object": "王", "platform": null}}
(分析: "电影"是分类，"王"是具体的标题过滤词。)

User: "搜索肖申克的救赎"
Expected: {{"category": null, "object": "肖申克的救赎", "platform": null}}
(分析: 没有明确说是电影还是书，Category 为空，直接搜名称。)

User: "给我看下所有的动作片"
Expected: {{"category": "电影", "object": "动作", "platform": null}}
(分析: "动作"是具体的流派标签，这里作为 Object 或 Tag 处理，视具体业务而定。如果作为标题关键词，提取为 Object。)

User: "找一下携程上关于日本的攻略"
Expected: {{"category": "攻略", "object": "日本", "platform": "ctrip"}}
--------------------------------------------------

User Query: {question}
请基于以上逻辑，严格按照 JSON 格式输出结果。
"""