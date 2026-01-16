import time
import json
from typing import Dict, Any, List, Optional
from DrissionPage.common import Settings
# 假设 BrowserDriver 在 drivers 目录下
from drivers.drission_driver import BrowserDriver 

class BrowserActor:
    """
    [行动执行单元]
    负责：点击、输入、滚动、导航、JavaScript代码执行
    """
    
    def __init__(self, tab):
        self.tab = tab
        # 设置 DrissionPage 的一些全局行为，例如不加载图片以加速（可选）
        # Settings.load_mode = 'eager' 

    def navigate(self, url: str):
        """打开指定 URL"""
        print(f"🚶 [Actor] Navigating to: {url}")
        self.tab.get(url)
        self.tab.wait.load_start()

    def perform_action(self, action_plan: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行单个原子动作
        :param action_plan: { "action": "click", "locator": "#btn", "value": "..." }
        """
        action_type = action_plan.get("action", "").lower()
        locator = action_plan.get("locator")
        value = action_plan.get("value")
        
        try:
            target_ele = None
            if locator:
                target_ele = self.tab.ele(locator)
            
            if action_type == "click":
                if target_ele:
                    # 优先使用 JS 点击，穿透力更强
                    target_ele.click(by_js=True)
                    # 智能等待：如果点击导致页面跳转
                    self.tab.wait.load_start() 
                    return {"status": "success", "msg": f"Clicked {locator}"}
                else:
                    return {"status": "failed", "msg": "Element not found"}

            elif action_type == "input":
                if target_ele:
                    target_ele.input(value)
                    return {"status": "success", "msg": f"Input '{value}' to {locator}"}
                
            elif action_type == "scroll":
                self.tab.scroll.to_bottom()
                return {"status": "success", "msg": "Scrolled to bottom"}

            elif action_type == "wait":
                time.sleep(int(value or 1))
                return {"status": "success", "msg": f"Waited {value}s"}

            else:
                return {"status": "error", "msg": f"Unknown action: {action_type}"}
                
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def execute_python_strategy(self, strategy_code: str, context: Dict = None) -> List[Dict]:
        """
        [高危能力] 执行 LLM 生成的 Python 代码 (原 main.py 的沙箱逻辑)
        """
        print("⚡ [Actor] Executing dynamic strategy...")
        
        local_scope = {
            "tab": self.tab,
            "results": [],
            "strategy": context or {},
            "time": time,
            "json": json
        }
        
        try:
            exec(strategy_code, {}, local_scope)
            return local_scope.get("results", [])
        except Exception as e:
            print(f"❌ Execution Error: {e}")
            return [{"error": str(e)}]