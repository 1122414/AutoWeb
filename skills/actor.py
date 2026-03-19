import io
import os
import time
import json
import sys
import contextlib
from typing import Dict, Any, List, Optional
from DrissionPage.common import Settings
from drivers.drission_driver import BrowserDriver
import skills.toolbox as toolbox
from skills.logger import logger, save_code_log


class _TeeStream:
    """将写入同时转发到多个流（用于实时控制台输出 + 日志捕获）。"""

    def __init__(self, *streams):
        self._streams = [s for s in streams if s is not None]

    def write(self, data):
        text = "" if data is None else str(data)
        for stream in self._streams:
            try:
                stream.write(text)
            except Exception:
                pass
        # 行级实时输出：遇到换行后立即刷新
        if "\n" in text or "\r" in text:
            self.flush()
        return len(text)

    def flush(self):
        for stream in self._streams:
            try:
                stream.flush()
            except Exception:
                pass


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

    def _safe_tab_url(self, tab) -> str:
        try:
            return str(tab.url or "")
        except Exception:
            return ""

    def _resolve_latest_tab(self):
        latest_tab = None
        if self.browser is not None:
            try:
                latest_tab = self.browser.latest_tab
            except Exception:
                latest_tab = None
        return latest_tab or self.tab

    def _wait_navigation_snapshot(
        self,
        start_tab,
        start_url: str,
        timeout_seconds: float = 8.0,
        poll_interval_seconds: float = 0.2,
    ):
        timeout = max(0.5, float(timeout_seconds))
        interval = max(0.05, float(poll_interval_seconds))
        deadline = time.time() + timeout

        last_tab = self._resolve_latest_tab()
        last_url = self._safe_tab_url(last_tab)

        while time.time() < deadline:
            current_tab = self._resolve_latest_tab()
            current_url = self._safe_tab_url(current_tab)

            tab_switched = current_tab is not start_tab
            url_changed = bool(start_url) and (current_url != start_url)

            last_tab = current_tab
            last_url = current_url

            if tab_switched or url_changed:
                try:
                    current_tab.wait.load_start(timeout=2)
                except Exception:
                    pass
                return current_tab, current_url, tab_switched, url_changed

            time.sleep(interval)

        final_tab = self._resolve_latest_tab()
        final_url = self._safe_tab_url(final_tab)
        return (
            final_tab,
            final_url,
            final_tab is not start_tab,
            bool(start_url) and (final_url != start_url),
        )

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

        start_tab = self._resolve_latest_tab()
        start_url = self._safe_tab_url(start_tab)
        logs = []
        output_buffer = io.StringIO()
        tee_stdout = _TeeStream(sys.stdout, output_buffer)
        tee_stderr = _TeeStream(sys.stderr, output_buffer)

        try:
            # 动态添加日志处理器，捕获当前执行上下文中所有 logger 输出
            import logging
            temp_handler = logging.StreamHandler(output_buffer)
            temp_handler.setLevel(logging.INFO)
            temp_handler.setFormatter(logging.Formatter("%(message)s"))
            logger._logger.addHandler(temp_handler)

            try:
                # 执行代码并捕获 print 和 logger 输出（同时实时回显到控制台）
                with contextlib.redirect_stdout(tee_stdout), contextlib.redirect_stderr(tee_stderr):
                    exec(strategy_code, {}, local_scope)
            finally:
                logger._logger.removeHandler(temp_handler)

            # 获取捕获的内容
            stdout_content = output_buffer.getvalue()
            if stdout_content:
                logs.append(f"--- [Code Output] ---\n{stdout_content.strip()}")

            # 检查 URL / 标签页变化（执行后重新采样 latest_tab，避免误判）
            end_tab, end_url, tab_switched, url_changed = self._wait_navigation_snapshot(
                start_tab=start_tab,
                start_url=start_url,
            )
            self.tab = end_tab

            if tab_switched and url_changed:
                logs.append(
                    f"--- [System Log] ---\nTab Switched + URL Changed: {start_url} -> {end_url}")
            elif tab_switched:
                logs.append(
                    f"--- [System Log] ---\nTab Switched: {start_url} -> {end_url}")
            elif url_changed:
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
