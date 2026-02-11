import os
import json
from typing import Optional
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

    def generate_expr(self, question: str) -> str:
        """
        分析用户问题，生成 Milvus expr 过滤表达式。

        通过字段注册表获取可用字段 → 注入 Prompt → LLM 生成 expr。
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
            # 尝试提取 JSON（处理 LLM 可能包裹 markdown 代码块的情况）
            json_str = raw_output
            if "```" in json_str:
                json_str = json_str.split("```")[1]
                if json_str.startswith("json"):
                    json_str = json_str[4:]
                json_str = json_str.strip()

            result = json.loads(json_str)
            expr = result.get("expr", "")
            search_query = result.get("search_query", question)

            if expr:
                print(f"🎯 Generated expr: \"{expr}\"")
                print(f"   Search query: \"{search_query}\"")
            else:
                print("   -> No filter, full search.")

            return expr

        except json.JSONDecodeError as e:
            print(f"⚠️ JSON parse failed: {e}, raw: {raw_output}")
            return ""
        except Exception as e:
            print(f"⚠️ Analysis failed: {e}")
            return ""


# 单例模式
query_analyzer = QueryAnalyzer()
