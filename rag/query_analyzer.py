import os
import json
from typing import Optional, Dict
from langchain_openai import ChatOpenAI

# 导入配置
from config import (
    MODEL_NAME, OPENAI_API_KEY, OPENAI_BASE_URL
)
from prompts.rag_prompts import QUERY_ANALYZER_PROMPT
from rag.field_registry import format_fields_for_prompt


class QueryAnalyzer:
    def __init__(self):
        self.llm = ChatOpenAI(
            model=MODEL_NAME,
            temperature=0,
            openai_api_key=OPENAI_API_KEY,
            openai_api_base=OPENAI_BASE_URL
        )

    def analyze(self, question: str) -> Dict:
        """
        分析用户问题，生成结构化查询参数。

        Returns:
            {
                "filter_expr": "category == 'movie'",  # 类目过滤（当前暂不启用）
                "search_query": "日本 攻略",            # 语义检索词
                "sort_field": "",                       # 排序字段（可选）
                "sort_order": ""                        # 排序方向（可选）
            }
        """
        print(f"🕵️ Analyzing query: {question}")
        try:
            # 1. 获取可用字段清单
            available_fields = format_fields_for_prompt()
            print(f"   📋 Available fields:\n      {available_fields}")

            # 2. 构建 Prompt 并调用 LLM
            prompt_text = QUERY_ANALYZER_PROMPT.format(
                available_fields=available_fields,
                question=question
            )

            response = self.llm.invoke(prompt_text)
            raw_output = response.content.strip()

            # 3. 解析 JSON 输出
            json_str = raw_output
            if "```" in json_str:
                json_str = json_str.split("```")[1]
                if json_str.startswith("json"):
                    json_str = json_str[4:]
                json_str = json_str.strip()

            result = json.loads(json_str)

            # 标准化输出
            analysis = {
                "filter_expr": result.get("filter_expr", ""),
                "search_query": result.get("search_query", question),
                "sort_field": result.get("sort_field", ""),
                "sort_order": result.get("sort_order", ""),
            }

            # 打印分析结果
            if analysis["filter_expr"]:
                print(f"🎯 Filter expr: \"{analysis['filter_expr']}\"")
            if analysis["sort_field"]:
                print(
                    f"📊 Sort: {analysis['sort_field']} ({analysis['sort_order']})")
            print(f"   Search query: \"{analysis['search_query']}\"")

            return analysis

        except json.JSONDecodeError as e:
            print(f"⚠️ JSON parse failed: {e}, raw: {raw_output}")
            return {"filter_expr": "", "search_query": question, "sort_field": "", "sort_order": ""}
        except Exception as e:
            print(f"⚠️ Analysis failed: {e}")
            return {"filter_expr": "", "search_query": question, "sort_field": "", "sort_order": ""}


# 单例模式
query_analyzer = QueryAnalyzer()
