import io
import os
import time
import json
import contextlib
from typing import Dict, Any, List, Optional
from DrissionPage.common import Settings
from drivers.drission_driver import BrowserDriver
import skills.toolbox as toolbox
from skills.logger import logger, save_code_log


class BrowserActor:
    """
    [行动执行单元]
    负责：点击、输入、滚动、导航、JavaScript代码执行
    """

    def __init__(self, tab, browser):
        self.tab = tab
        self.browser = browser

    def navigate(self, url: str) -> None:
        """打开指定 URL"""
        logger.info(f"🚶 [Actor] Navigating to: {url}")
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
                    target_ele.click(by_js=True)
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
            logger.warning(f"[Actor] Action failed: {e}")
            return {"status": "error", "msg": str(e)}

    def execute_python_strategy(self, strategy_code: str, context: Dict = None) -> Dict[str, Any]:
        """
        [高危能力] 执行 LLM 生成的 Python 代码

        Returns:
            Dict: {
                "result_data": List[Dict],
                "execution_log": str
            }
        """
        logger.info("⚡ [Actor] Executing dynamic strategy...")

        local_scope = {
            "tab": self.tab,
            "results": [],
            "strategy": context or {},
            "time": time,
            "json": json,
            "toolbox": toolbox,
            "browser": self.browser,
        }

        start_url = self.tab.url
        logs = []
        output_buffer = io.StringIO()

        try:
            # 动态添加日志处理器，捕获当前执行上下文中所有 logger 输出
            import logging
            temp_handler = logging.StreamHandler(output_buffer)
            temp_handler.setLevel(logging.INFO)
            temp_handler.setFormatter(logging.Formatter("%(message)s"))
            logger._logger.addHandler(temp_handler)

            try:
                # 执行代码并捕获 print 和 logger 输出
                with contextlib.redirect_stdout(output_buffer), contextlib.redirect_stderr(output_buffer):
                    exec(strategy_code, {}, local_scope)
            finally:
                logger._logger.removeHandler(temp_handler)

            # 获取捕获的内容
            stdout_content = output_buffer.getvalue()
            if stdout_content:
                logs.append(f"--- [Code Output] ---\n{stdout_content.strip()}")

            # 检查 URL 变化
            self.tab.wait(5)
            end_url = self.tab.url
            if start_url != end_url:
                logs.append(
                    f"--- [System Log] ---\nURL Changed: {start_url} -> {end_url}")
            else:
                logs.append(f"--- [System Log] ---\nURL Unchanged: {end_url}")

            # 保存代码执行日志到 code_log 目录
            log_path = save_code_log(
                code=strategy_code,
                output="\n".join(logs),
                is_error=False,
                extra_info=f"URL: {start_url} -> {end_url}"
            )
            if log_path:
                logger.info(f"📄 [Actor] Log saved to: {log_path}")
                logs.append(f"--- [System Log] ---\nLog saved to: {log_path}")

            return {
                "result_data": local_scope.get("results", []),
                "execution_log": "\n".join(logs)
            }

        except Exception as e:
            error_msg = f"❌ Execution Error: {e}"
            logger.error(error_msg)

            logs.append(
                f"--- [Code Output (Partial)] ---\n{output_buffer.getvalue()}")
            logs.append(error_msg)

            # 保存错误日志
            log_path = save_code_log(
                code=strategy_code,
                output="\n".join(logs),
                is_error=True
            )
            if log_path:
                logger.info(f"📄 [Actor] Error Log saved to: {log_path}")

            return {
                "result_data": local_scope.get("results", []),
                "execution_log": "\n".join(logs)
            }
