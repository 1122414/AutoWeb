# ==============================================================================
# AutoWeb 统一日志模块
# ==============================================================================
# 功能：
# - 系统运行日志: logs/sys_log/YYYYMMDD/autoweb_HHMMSS.log (每次运行独立文件)
# - 代码执行日志: logs/code_log/YYYYMMDD/exec_HHMMSS.log (Python executor)
# - dp_cli执行日志: logs/code_log/YYYYMMDD/dpcli_HHMMSS.log (dp_cli 命令)
# - 同时输出到控制台和文件
# ==============================================================================

import os
import logging
import time
import functools
import inspect
from typing import Optional, Callable, List

# 日志根目录
LOG_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
SYS_LOG_DIR = os.path.join(LOG_ROOT, "sys_log")
CODE_LOG_DIR = os.path.join(LOG_ROOT, "code_log")

# 确保日志目录存在
os.makedirs(SYS_LOG_DIR, exist_ok=True)
os.makedirs(CODE_LOG_DIR, exist_ok=True)

# 本次运行的启动时间戳（用于生成 per-run 文件名）
_RUN_TS = time.time()
_RUN_DATE = time.strftime("%Y%m%d", time.localtime(_RUN_TS))
_RUN_TIME = time.strftime("%H%M%S", time.localtime(_RUN_TS))


class AutoWebLogger:
    """
    AutoWeb 日志管理器 — 每次运行自动创建独立 sys_log 文件。

    使用方式:
        from skills.logger import logger
        logger.info("这是一条日志")
    """

    _instance: Optional['AutoWebLogger'] = None
    _logger: Optional[logging.Logger] = None
    _sys_log_path: Optional[str] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_logger()
        return cls._instance

    @classmethod
    def reset(cls):
        """重置单例（仅用于测试）"""
        if cls._instance is not None and cls._instance._logger is not None:
            for h in list(cls._instance._logger.handlers):
                cls._instance._logger.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        cls._instance = None

    def _init_logger(self):
        """初始化系统日志器 — 每次运行创建独立文件"""
        self._logger = logging.getLogger("AutoWeb")
        self._logger.setLevel(logging.DEBUG)

        # 防止重复添加 handler
        if self._logger.handlers:
            return

        # 日志格式 — 不包含 %(funcName)s:%(lineno)d，避免与 trace_log 重复
        console_formatter = logging.Formatter(
            fmt="%(message)s"
        )
        file_formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        # 1. 控制台 Handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(console_formatter)

        # 2. 文件 Handler — 每次运行独立文件
        date_dir = os.path.join(SYS_LOG_DIR, _RUN_DATE)
        os.makedirs(date_dir, exist_ok=True)
        self._sys_log_path = os.path.join(date_dir, f"autoweb_{_RUN_TIME}.log")
        file_handler = logging.FileHandler(
            filename=self._sys_log_path,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(file_formatter)

        self._logger.addHandler(console_handler)
        self._logger.addHandler(file_handler)

    @property
    def sys_log_path(self) -> Optional[str]:
        return self._sys_log_path

    # ==================== 日志方法代理 ====================

    def debug(self, msg: str, *args, **kwargs):
        self._logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs):
        self._logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        self._logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        self._logger.error(msg, *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs):
        self._logger.exception(msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs):
        self._logger.critical(msg, *args, **kwargs)


# ==================== 调用追踪工具 ====================


def trace_log(msg: str = "", level: str = "info", *, stacklevel: int = 2):
    """
    使用调用者的函数名和行号记录日志到 sys_log。

    输出格式:
        [create_llm:38] LLM invoke starting...
    """
    caller_frame = inspect.currentframe()
    for _ in range(stacklevel - 1):
        if caller_frame is not None:
            caller_frame = caller_frame.f_back
    if caller_frame is not None:
        func_name = caller_frame.f_code.co_name
        line_no = caller_frame.f_lineno
        prefixed_msg = f"[{func_name}:{line_no}] {msg}" if msg else f"[{func_name}:{line_no}]"
    else:
        prefixed_msg = msg

    log_func = getattr(logger, level, logger.info)
    log_func(prefixed_msg)


def log_call(level: str = "debug"):
    """
    装饰器：自动记录函数入口和出口。

    输出格式:
        [execute_action:205] -> ENTER execute_action()
        [execute_action:205] <- EXIT  execute_action() [0.523s]
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            trace_log(f"-> ENTER {func.__name__}()", level=level)
            start = time.time()
            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start
                trace_log(f"<- EXIT  {func.__name__}() [{elapsed:.3f}s]", level=level)
                return result
            except Exception as e:
                elapsed = time.time() - start
                trace_log(f"<- ERROR {func.__name__}() [{elapsed:.3f}s] {type(e).__name__}: {e}", level="error")
                raise

        return wrapper

    return decorator


# ==================== 代码执行日志工具 ====================


def save_code_log(
    code: str,
    output: str,
    is_error: bool = False,
    extra_info: Optional[str] = None
) -> str:
    """
    保存 Python 代码执行日志到 logs/code_log/ 目录。

    Returns:
        日志文件的绝对路径
    """
    date_str = time.strftime("%Y%m%d")
    time_str = time.strftime("%H%M%S")
    daily_dir = os.path.join(CODE_LOG_DIR, date_str)
    os.makedirs(daily_dir, exist_ok=True)
    prefix = "error" if is_error else "exec"
    log_file = os.path.join(daily_dir, f"{prefix}_{time_str}.log")

    content_parts = [
        f"--- [Generated Code] ---",
        code,
        "",
        f"--- [Execution Output] ---",
        output
    ]

    if extra_info:
        content_parts.extend(["", f"--- [Extra Info] ---", extra_info])

    try:
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("\n".join(content_parts))
        return os.path.abspath(log_file)
    except IOError as e:
        logger.warning(f"Failed to save code log: {e}")
        return ""


def save_dpcli_code_log(
    cmd: List[str],
    stdout: str,
    stderr: str,
    returncode: Optional[int],
    timed_out: bool = False,
    elapsed: float = 0.0,
    extra_info: Optional[str] = None,
) -> str:
    """
    保存 dp_cli 命令执行日志到 logs/code_log/YYYYMMDD/dpcli_HHMMSS.log

    Args:
        cmd: 完整命令行
        stdout: 标准输出
        stderr: 标准错误
        returncode: 返回码（None 表示未完成）
        timed_out: 是否超时
        elapsed: 执行耗时（秒）
        extra_info: 额外上下文信息

    Returns:
        日志文件的绝对路径
    """
    date_str = time.strftime("%Y%m%d")
    time_str = time.strftime("%H%M%S")
    daily_dir = os.path.join(CODE_LOG_DIR, date_str)
    os.makedirs(daily_dir, exist_ok=True)
    log_file = os.path.join(daily_dir, f"dpcli_{time_str}.log")

    parts = [
        f"--- [dp_cli Command] ---",
        " ".join(cmd),
        "",
        f"--- [Execution Info] ---",
        f"Return Code: {returncode}",
        f"Timed Out: {timed_out}",
        f"Elapsed: {elapsed:.3f}s",
        "",
        f"--- [stdout] ---",
        stdout or "(empty)",
        "",
        f"--- [stderr] ---",
        stderr or "(empty)",
    ]

    if extra_info:
        parts.extend(["", f"--- [Extra Info] ---", extra_info])

    try:
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("\n".join(parts))
        return os.path.abspath(log_file)
    except IOError as e:
        logger.warning(f"Failed to save dp_cli code log: {e}")
        return ""


# ==================== 全局单例 ====================

logger = AutoWebLogger()
