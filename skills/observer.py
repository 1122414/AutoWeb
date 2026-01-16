import json
import re
import time
from typing import Dict, Any, Union, Optional
from langchain_openai import ChatOpenAI
from langchain_core.documents import Document

# 引入 Prompt
from prompts.dom_prompts import DOM_ANALYSIS_PROMPT, DRISSION_LOCATOR_PROMPT
from drivers.js_loader import DOM_SKELETON_JS  # 假设你把 JS 放在了这里
from config import MODEL_NAME, OPENAI_API_KEY, OPENAI_BASE_URL

class BrowserObserver:
    """
    [视觉感知单元]
    负责：页面结构分析、元素定位策略生成、非结构化数据清洗
    """
    def __init__(self):
        self.llm = ChatOpenAI(
            model=MODEL_NAME, 
            temperature=0, 
            openai_api_key=OPENAI_API_KEY, 
            openai_api_base=OPENAI_BASE_URL
        )

    # ================= 工具函数 (原 dom_helper/extractor_utils) =================
    
    def _clean_text(self, text: str) -> str:
        """基础文本清洗：去除多余空白"""
        if not text: return ""
        return re.sub(r'\s+', ' ', text).strip()

    def _parse_json_safely(self, text: str) -> Union[Dict, list]:
        """[核心能力] 鲁棒的 JSON 解析器，能处理 LLM 返回的不规范 JSON"""
        text = text.strip()
        # 尝试直接解析
        try: return json.loads(text)
        except: pass
        
        # 尝试清洗 Markdown 标记
        cleaned = text.replace("```json", "").replace("```", "").strip()
        try: return json.loads(cleaned)
        except: pass
        
        # 尝试正则提取 {} 或 []
        try:
            match = re.search(r'\{[\s\S]*\}', text) 
            if match: return json.loads(match.group(0))
        except: pass
        
        return {"error": "Failed to parse JSON", "raw": text}

    # ================= 核心感知能力 =================

    def capture_dom_skeleton(self, tab) -> str:
        """
        [视觉] 获取当前页面的 DOM 骨架
        直接调用注入的 JS，不再使用 Python 进行繁重的 lxml 解析
        """
        try:
            # 注入 JS (如果页面刷新了需要重新注入，DrissionPage run_js 会自动处理上下文)
            # 注意：DOM_SKELETON_JS 应该是一个完整的 IIFE 脚本字符串
            raw_result = tab.run_js(DOM_SKELETON_JS)
            
            # JS 返回的可能已经是字符串化的 JSON，也可能是对象
            if isinstance(raw_result, str):
                return raw_result
            return json.dumps(raw_result, ensure_ascii=False)
            
        except Exception as e:
            print(f"⚠️ DOM Skeleton Capture Failed: {e}")
            return json.dumps({"error": str(e)})

    def analyze_locator_strategy(self, dom_skeleton: str, requirement: str) -> Dict:
        """
        [推理] 基于 DOM 骨架和用户需求，生成操作定位策略
        """
        prompt = DRISSION_LOCATOR_PROMPT.format(
            requirement=requirement,
            dom_json=dom_skeleton[:30000] # 防止 Token 溢出
        )
        
        response = self.llm.invoke(prompt)
        strategy = self._parse_json_safely(response.content)
        
        print(f"🧠 [Observer] 定位策略生成: {strategy.get('locator', 'N/A')}")
        return strategy

    def extract_structured_data(self, html_snippet: str, schema_desc: str) -> Dict:
        """
        [清洗] 从 HTML 片段中提取结构化数据 (原 extractor_agent 核心逻辑)
        """
        # 这里可以使用更通用的 UNIVERSAL_EXTRACTION_PROMPT
        # 为了演示，暂时简化逻辑
        pass