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

    def execute_python_strategy(self, strategy_code: str, context: Dict = None) -> Dict[str, Any]:
        """
        [高危能力] 执行 LLM 生成的 Python 代码 (原 main.py 的沙箱逻辑)
        
        Returns:
            Dict: {
                "result_data": List[Dict],  # 爬取的数据 results
                "execution_log": str        # 捕获的 print 日志 + 系统日志
            }
        """
        print("⚡ [Actor] Executing dynamic strategy...")
        
        # [Added] Import Toolbox Wrapper
        import skills.toolbox as toolbox
        
        local_scope = {
            "tab": self.tab,
            "results": [],
            "strategy": context or {},
            "time": time,
            "json": json,
            "toolbox": toolbox, # Inject the "Arms"
            "save_data": toolbox.save_data, # [Fix] Fail-safe alias
            "save_to_csv": toolbox.save_to_csv, # [Fix] Fail-safe alias for legacy calls
            "http_request": toolbox.http_request # [Fix] Fail-safe alias
        }
        
        # 1. 记录初始状态
        start_url = self.tab.url
        logs = []
        
        # [Log Code Content] - ONLY for file, not for UI
        # logs.append(f"--- [Generated Code] ---\n{strategy_code}\n") 
        
        import io
        import contextlib
        
        output_buffer = io.StringIO()
        
        try:
            # 2. 执行代码并捕获 print 输出
            with contextlib.redirect_stdout(output_buffer):
                exec(strategy_code, {}, local_scope)
            
            # ... (Execution logic remains) ...
            
            # 获取捕获的 print 内容
            stdout_content = output_buffer.getvalue()
            if stdout_content:
                logs.append(f"--- [Code Output] ---\n{stdout_content.strip()}")
            
            # 3. 检查 URL 变化
            end_url = self.tab.url
            if start_url != end_url:
                logs.append(f"--- [System Log] ---\nURL Changed: {start_url} -> {end_url}")
            else:
                logs.append(f"--- [System Log] ---\nURL Unchanged: {end_url}")
            
            # [Added] Persistent Logging
            import os
            log_dir = "logs"
            try:
                if not os.path.exists(log_dir):
                    os.makedirs(log_dir)
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                log_file = os.path.join(log_dir, f"exec_{timestamp}.log")
                
                # [Crucial Change] Prepend Code ONLY to the file content
                file_content = f"--- [Generated Code] ---\n{strategy_code}\n\n" + "\n".join(logs)
                
                with open(log_file, "w", encoding="utf-8") as f:
                    f.write(file_content)
                    
                print(f"📄 [Actor] Log saved to: {log_file}")
                # Append log path to execution_log so user can see it in UI too
                logs.append(f"--- [System Log] ---\nLog saved to: {os.path.abspath(log_file)}")
            except Exception as e:
                print(f"⚠️ Failed to save log file: {e}")

            return {
                "result_data": local_scope.get("results", []),
                "execution_log": "\n".join(logs)
            }
            
        except Exception as e:
            error_msg = f"❌ Execution Error: {e}"
            print(error_msg)
            # 即使出错，也要把已打印的内容返回
            logs.append(f"--- [Code Output (Partial)] ---\n{output_buffer.getvalue()}")
            logs.append(error_msg)
            
            # [Added] Save Error Log
            import os
            try:
                log_dir = "logs"
                if not os.path.exists(log_dir):
                    os.makedirs(log_dir)
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                log_file = os.path.join(log_dir, f"error_{timestamp}.log")
                
                # [Crucial Change] Prepend Code to error log too
                file_content = f"--- [Generated Code] ---\n{strategy_code}\n\n" + "\n".join(logs)
                
                with open(log_file, "w", encoding="utf-8") as f:
                    f.write(file_content)
                print(f"📄 [Actor] Error Log saved to: {log_file}")
            except:
                pass

            return {
                "result_data": local_scope.get("results", []),
                "execution_log": "\n".join(logs)
            }
