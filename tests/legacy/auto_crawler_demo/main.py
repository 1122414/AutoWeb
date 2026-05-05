import os
import json
import time
import datetime
from DrissionPage import Chromium, ChromiumOptions
from openai import OpenAI
from dotenv import load_dotenv
from dom_utils import DOM_SKELETON_JS
from prompt_template import XPATH_ANALYSIS_PROMPT, SCRAWL_DATA_SYSTEM_PROMPT

# 加载环境变量 (需要 .env 文件中有 OPENAI_API_KEY)
load_dotenv()

class AutonomousCrawler:
    def __init__(self):
        # 1. 初始化大模型客户端
        self.client = OpenAI(
            # 请替换为你的实际 API Key
            api_key='ms-16ce71e9-8c5b-444b-b793-d08654979f72', 
            base_url='https://api-inference.modelscope.cn/v1'
        )
        self.model_name = "Qwen/Qwen3-235B-A22B-Instruct-2507" 

        # 2. 初始化浏览器 (DrissionPage)
        co = ChromiumOptions()
        co.set_argument('--no-sandbox')
        self.browser = Chromium(addr_or_opts=co)
        self.tab = self.browser.latest_tab

        # 3. 加载外部 JS 文件
        self.dom_js_code = DOM_SKELETON_JS

    def _load_js_file(self, filename):
        """读取 JS 文件内容"""
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            print(f"❌ 错误：找不到文件 {filename}，请确保它与脚本在同一目录下。")
            return ""

    def analyze_and_crawl(self, url: str, requirement: str):
        if not self.dom_js_code:
            print("❌ JS 代码未加载，无法继续。")
            return

        print(f"🚀 [Step 1] 打开页面: {url}")
        self.tab.get(url)
        self.tab.wait.load_start() 
        time.sleep(2) # 等待动态内容渲染

        # --- Step 2: 注入视觉神经 ---
        print(f"👁️ [Step 2] 注入视觉神经 (JS)...")
        self.tab.run_js(self.dom_js_code)
        
        # 轮询等待 JS 结果
        start_time = time.time()
        timeout = 10  
        dom_json_str = None
        
        while time.time() - start_time < timeout:
            status = self.tab.run_js("return window.__dom_status;")
            if status == 'success':
                dom_json_str = self.tab.run_js("return window.__dom_result;")
                break
            elif status == 'error':
                error_msg = self.tab.run_js("return window.__dom_result;")
                print(f"❌ JS 内部报错: {error_msg}")
                return
            time.sleep(0.5)
            
        # 清理全局变量
        self.tab.run_js("delete window.__dom_result; delete window.__dom_status;")

        if not dom_json_str:
            print("❌ JS 返回了 None, 可能是超时或页面完全空白")
            return

        print(f"   -> 骨架获取成功 (大小: {len(dom_json_str)} chars)")

        # --- Step 3: LLM 思考与代码生成 ---
        print(f"🧠 [Step 3.1] 正在分析 DOM 结构，提取 XPath 策略...")
        xpath_plan_str = self._step_1_analyze_dom(dom_json_str, requirement)
        
        # 解析策略 JSON
        strategy_dict = {}
        try:
            # 清洗可能存在的 Markdown 标记
            clean_json = xpath_plan_str.replace("```json", "").replace("```", "").strip()
            start_idx = clean_json.find('{')
            end_idx = clean_json.rfind('}') + 1
            if start_idx != -1 and end_idx != -1:
                clean_json = clean_json[start_idx:end_idx]
            
            strategy_dict = json.loads(clean_json)
            print(f"📋 [Strategy] 策略已生成: {list(strategy_dict.keys())}")
        except json.JSONDecodeError:
            print(f"❌ 策略 JSON 解析失败，原始返回:\n{xpath_plan_str}")
            return

        print(f"⌨️ [Step 3.2] 正在根据策略生成 Python 代码...")
        code = self._step_2_generate_code(requirement, xpath_plan_str)
        
        # --- Step 5: 执行代码 ---
        print(f"⚡ [Step 4] 注入策略并执行代码...")
        print("-" * 40)
        print(code)
        print("-" * 40)
        
        # 传入 strategy_dict
        self._execute_generated_code(self.tab, code, strategy_dict)

    def _step_1_analyze_dom(self, dom_json: str, requirement: str) -> str:
        """Step 1: DOM 分析器"""
        safe_dom = dom_json[:40000] # 截断防止溢出

        prompt = XPATH_ANALYSIS_PROMPT.format(
            requirement=user_requirement,
            dom_json=dom_json
        )
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"} 
        )
        return response.choices[0].message.content.strip()

    def _step_2_generate_code(self, requirement: str, xpath_plan: str) -> str:
        """Step 2: 代码生成器"""
        
        prompt = SCRAWL_DATA_SYSTEM_PROMPT.format(
            requirement=requirement,
            xpath_plan=xpath_plan
        )
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        code = response.choices[0].message.content
        code = code.replace("```python", "").replace("```", "").replace("json", "").strip()
        return code

    def _execute_generated_code(self, page_obj, code_str, strategy_data):
        """沙箱执行器"""
        print("⚡ [Debug] 准备执行代码...")
        
        local_scope = {
            "tab": page_obj,    
            "results": [],      
            "strategy": strategy_data, 
            "json": json,
            "time": time
        }
        
        try:
            exec(code_str, {}, local_scope)
            
            results = local_scope.get("results", [])
            
            if not results:
                print("⚠️ 代码执行完毕，但 results 为空。")
            else:
                print(f"\n🎉 抓取成功！共 {len(results)} 条数据。")
                for i, item in enumerate(results[:3]):
                    print(f" [{i+1}] {item}")
                if len(results) > 3:
                    print(f" ... (还有 {len(results)-3} 条)")
                    
        except Exception as e:
            print(f"❌ 代码执行出错: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    crawler = AutonomousCrawler()
    
    # target_url = "https://ssr1.scrape.center/"
    # TODO 1.16 想法 是不是拆成TODO好一点
    # user_requirement = "爬取前五页的电影名称、评分、链接、图片的链接"

    target_url = "https://www.wangfei.la/"
    user_requirement = "点击各个电影进入详情页，获取简介。"
    
    crawler.analyze_and_crawl(target_url, user_requirement)