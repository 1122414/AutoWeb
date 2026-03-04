import json
import re
import time
from typing import Dict, Any, Union, Optional
from langchain_openai import ChatOpenAI
from langchain_core.documents import Document

# 引入 Prompt
from prompts.observer_prompts import DRISSION_LOCATOR_PROMPT
from drivers.js_loader import DOM_SKELETON_JS
from config import OBSERVER_MODEL_NAME, OBSERVER_API_KEY, OBSERVER_BASE_URL

# 引入 Compressor
from skills.dom_compressor import DOMCompressor


class BrowserObserver:
    """
    [视觉感知单元]
    负责：页面结构分析、元素定位策略生成、非结构化数据清洗
    """

    def __init__(self):
        self.llm = ChatOpenAI(
            model=OBSERVER_MODEL_NAME,
            temperature=0,
            openai_api_key=OBSERVER_API_KEY,
            openai_api_base=OBSERVER_BASE_URL,
            streaming=True
        )
        # [Optimization] DOM Cache
        self._dom_cache = {"hash": None, "analysis": None}
        # [Optimization] Compressor (Default Lite)
        self.compressor = DOMCompressor(mode="lite")

    # ================= 工具函数 (原 dom_helper/extractor_utils) =================

    def _clean_text(self, text: str) -> str:
        """基础文本清洗：去除多余空白"""
        if not text:
            return ""
        return re.sub(r'\s+', ' ', text).strip()

    def _parse_json_safely(self, text: str) -> Union[Dict, list]:
        """[核心能力] 鲁棒的 JSON 解析器，能处理 LLM 返回的不规范 JSON"""
        text = text.strip()
        # 尝试直接解析
        try:
            return json.loads(text)
        except:
            pass

        # 尝试清洗 Markdown 标记
        cleaned = text.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(cleaned)
        except:
            pass

        # 尝试正则提取 {} 或 []
        try:
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                return json.loads(match.group(0))
        except:
            pass

        # 2. 尝试直接解析 (Best Case)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 3. 针对“多个 JSON 对象堆叠”的补救 (即你遇到的情况)
        # 现象：text 是 '{"a":1}\n{"b":2}' 或 '{"a":1}{"b":2}'
        # 对策：正则查找 `} <空白> {`，替换为 `}, {`，然后两头加 []
        try:
            # 检查是否存在两个对象的边界
            # 正则解释：查找 } 后面跟着任意空白字符(包括换行)，然后是 {
            if re.search(r'\}\s*\{', text):
                print("🔧 [Observer] 检测到多个独立 JSON 对象，尝试自动合并为列表...")
                # 将边界替换为逗号，并确保两边有空格
                fixed_text = re.sub(r'\}\s*\{', '}, {', text)
                # 包裹为列表
                fixed_text = f"[{fixed_text}]"
                return json.loads(fixed_text)
        except Exception as e:
            # 不要让这里报错中断流程，继续尝试下一种方法
            pass

        # 4. 暴力提取 (Last Resort)
        # 如果还是不行，用正则把所有 {...} 抠出来，一个个解析再组装
        try:
            # 匹配最外层的 {}
            # 注意：这个正则可能处理不了嵌套很深且格式极其混乱的情况，但在大多数 LLM 输出场景下足够用
            matches = re.findall(r'(\{[\s\S]*?\})(?=\s*\{|\s*$)', text)

            # 如果上面的正则没匹配到，尝试更简单的贪婪匹配
            if not matches:
                matches = re.findall(r'\{[\s\S]*?\}', text)

            valid_objs = []
            for m in matches:
                try:
                    # 验证每个片段是否是合法 JSON
                    obj = json.loads(m)
                    valid_objs.append(obj)
                except:
                    continue

            if valid_objs:
                print(f"🔧 [Observer] 暴力提取成功，回收了 {len(valid_objs)} 个对象")
                # 如果只有一个对象且原意可能不是列表，视情况返回（但为了统一，返回列表通常更安全）
                # 这里为了兼容你的 Prompt 定义 (return List)，直接返回列表
                return valid_objs
        except:
            pass

        # 如果所有尝试都失败了
        return {"error": "Failed to parse JSON", "raw": text}

    # ================= 核心感知能力 =================

    def capture_dom_skeleton(self, tab) -> str:
        """
        [视觉] 获取当前页面的 DOM 骨架
        直接调用注入的 JS
        包含重试机制，应对动态页面渲染延迟
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # 注入 JS
                tab.run_js(DOM_SKELETON_JS)

                # 轮询等待 JS 结果
                start_time = time.time()
                timeout = 10
                dom_json_str = None

                while time.time() - start_time < timeout:
                    status = tab.run_js("return window.__dom_status;")
                    if status == 'success':
                        dom_json_str = tab.run_js(
                            "return window.__dom_result;")
                        break
                    elif status == 'error':
                        error_msg = tab.run_js("return window.__dom_result;")
                        print(
                            f"   ⚠️ JS 内部报错 (Attempt {attempt+1}): {error_msg}")
                        break
                    time.sleep(0.5)

                # 清理全局变量
                tab.run_js(
                    "delete window.__dom_result; delete window.__dom_status;")

                # 检查结果有效性
                if dom_json_str:
                    if isinstance(dom_json_str, str) and "Empty DOM" in dom_json_str:
                        print(
                            f"   ⚠️ 检测到 Empty DOM (Attempt {attempt+1})，等待 1s 后重试...")
                        time.sleep(1.0)
                        continue

                    # 1. 解析原始 JSON
                    raw_dom = dom_json_str
                    if isinstance(dom_json_str, str):
                        try:
                            raw_dom = json.loads(dom_json_str)
                        except:
                            return dom_json_str  # Fallback

                    # 2. 调用压缩器 (Compress)
                    print(
                        f"   📉 [Observer] Compressing DOM (Original Size: {len(str(raw_dom))} chars)...")
                    with open("raw_dom.json", "w", encoding="utf-8") as f:
                        json.dump(raw_dom, f, ensure_ascii=False, indent=4)
                    compressed_dom = self.compressor.compress(raw_dom)
                    compressed_str = json.dumps(
                        compressed_dom, ensure_ascii=False)
                    print(
                        f"   📉 [Observer] Compression Done (New Size: {len(compressed_str)} chars).")

                    return compressed_str
                else:
                    print(f"   ⚠️ JS 执行超时 (Attempt {attempt+1})")

            except Exception as e:
                print(f"   ⚠️ DOM Capture Failed (Attempt {attempt+1}): {e}")
                time.sleep(1.0)

        return json.dumps({"error": "Failed to capture DOM after retries"})

    def analyze_locator_strategy(self, dom_skeleton: str, requirement: str, current_url: str, previous_steps: list = [], ignore_cache: bool = False, previous_failures: list = None) -> Union[Dict, list]:
        """
        [推理] 基于 DOM 骨架和用户需求，生成操作定位策略
        [Optimization] 增加 MD5 缓存机制 & 启发式搜索
        """
        # 1. [Local Tool] 启发式搜索 (Heuristic Search)
        # 如果需求非常明确 (如 "点击 '登录'"), 且页面刚好有这个文本，直接返回，不消耗 Token
        try:
            import re
            # 提取需求中的引用文本， e.g. "点击 '确 定'" -> "确 定"
            # 匹配单引号或双引号中的内容
            match_req = re.search(r"['“](.+?)['”]", requirement)
            if match_req:
                target_text = match_req.group(1)
                # 简单清洗
                target_text = target_text.strip()

                # 检查是否有序号限定词（如"第一条"、"第二个"等），有则跳过启发式
                ordinal_keywords = ["第一", "第二", "第三", "第1", "第2",
                                    "第3", "首个", "最后", "first", "second", "last"]
                has_ordinal = any(kw in requirement for kw in ordinal_keywords)

                if len(target_text) > 1 and "dom_json" not in requirement and not has_ordinal:
                    # 统计目标文本在 DOM 中出现的次数
                    occurrence_count = dom_skeleton.count(f'"{target_text}"')

                    if occurrence_count == 1:
                        # 唯一出现，可以安全使用启发式匹配
                        print(
                            f"⚡ [Observer] Heuristic Hit! Found unique text '{target_text}' in DOM.")
                        return {"locator": f"text={target_text}", "reason": "Heuristic Match (Unique)"}
                    elif occurrence_count > 1:
                        # 多次出现，需要 LLM 分析来选择正确的元素
                        print(
                            f"🔍 [Observer] Text '{target_text}' appears {occurrence_count} times, using LLM analysis...")
        except Exception as e:
            pass

        try:
            import hashlib
            # 计算 Hash (Include previous_steps in hash to distinguish context)
            context_str = f"{dom_skeleton}|{requirement}|{str(previous_steps)}"
            current_hash = hashlib.md5(context_str.encode('utf-8')).hexdigest()

            # 检查缓存: 如果 DOM Hash 一致，且缓存中有有效结果，直接返回
            if not ignore_cache and self._dom_cache["hash"] == current_hash and self._dom_cache["analysis"]:
                print(
                    f"⏩ [Observer] DOM Cache Hit! ({current_hash[:8]}) - Skipping LLM Analysis")
                return self._dom_cache["analysis"]

        except Exception as e:
            print(f"⚠️ Cache Check Failed: {e}")

        # Formatted previous steps and failures
        prev_steps_str = "\n".join(
            [f"- {s}" for s in previous_steps]) if previous_steps else "(无 - 初始状态)"
        prev_failures_str = "\n".join(
            [f"- {f}" for f in previous_failures]) if previous_failures else "(无失败记录)"

        # Cache Miss - Call LLM
        prompt = DRISSION_LOCATOR_PROMPT.format(
            requirement=requirement,
            current_url=current_url,
            previous_steps=prev_steps_str,
            previous_failures=prev_failures_str,
            dom_json=dom_skeleton[:50000]  # 防止 Token 溢出
        )

        response = self.llm.invoke(prompt)
        strategy = self._parse_json_safely(response.content)

        # Update Cache
        try:
            self._dom_cache["hash"] = current_hash
            self._dom_cache["analysis"] = strategy
        except:
            pass

        if isinstance(strategy, dict):
            print(f"🧠 [Observer] 定位策略生成: {strategy.get('locator', 'N/A')}")
        elif isinstance(strategy, list) and len(strategy) > 0:
            print(
                f"🧠 [Observer] 定位策略生成 (List): {len(strategy)} items, First: {strategy[0].get('locator', 'N/A')}")
        else:
            print(f"🧠 [Observer] 定位策略生成: {strategy}")

        return strategy
