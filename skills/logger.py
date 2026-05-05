# ==============================================================================
# AutoWeb 统一日志模块
# ==============================================================================
# 功能：
# - 系统运行日志: logs/sys_log/autoweb_YYYYMMDD.log (按天轮转)
# - 代码执行日志: logs/code_log/YYYYMMDD/exec_HHMMSS.log (按天分目录)
# - 同时输出到控制台和文件
# - 文件日志自动包含函数名和行号，便于追踪定位
# ==============================================================================

import os
import logging
import time
import functools
import inspect
from logging.handlers import TimedRotatingFileHandler
from typing import Optional, Callable

# 日志根目录
LOG_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
SYS_LOG_DIR = os.path.join(LOG_ROOT, "sys_log")
CODE_LOG_DIR = os.path.join(LOG_ROOT, "code_log")

# 确保日志目录存在
os.makedirs(SYS_LOG_DIR, exist_ok=True)
os.makedirs(CODE_LOG_DIR, exist_ok=True)


class AutoWebLogger:
    """
    AutoWeb 日志管理器

    使用方式:
        from skills.logger import logger
        logger.info("这是一条日志")
    """

    _instance: Optional['AutoWebLogger'] = None
    _logger: Optional[logging.Logger] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_logger()
        return cls._instance

    def _init_logger(self):
        """初始化系统日志器"""
        self._logger = logging.getLogger("AutoWeb")
        self._logger.setLevel(logging.DEBUG)

        # 防止重复添加 handler
        if self._logger.handlers:
            return

        # 日志格式
        console_formatter = logging.Formatter(
            fmt="%(message)s"  # 控制台保持简洁，与原 print 风格一致
        )
        file_formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        # 1. 控制台 Handler (保持彩色输出)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(console_formatter)

        # 2. 系统日志文件 Handler (按天轮转)
        sys_log_file = os.path.join(SYS_LOG_DIR, "autoweb.log")
        file_handler = TimedRotatingFileHandler(
            filename=sys_log_file,
            when="midnight",
            interval=1,
            backupCount=30,  # 保留30天
            encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(file_formatter)
        file_handler.suffix = "%Y%m%d"  # 日志文件名后缀格式

        self._logger.addHandler(console_handler)
        self._logger.addHandler(file_handler)

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
        """记录异常信息（自动附加堆栈）"""
        self._logger.exception(msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs):
        self._logger.critical(msg, *args, **kwargs)


# ==================== 调用追踪工具 ====================


def trace_log(msg: str = "", level: str = "info", *, stacklevel: int = 2):
    """
    使用调用者的函数名和行号记录日志。

    Usage:
        from skills.logger import trace_log
        trace_log("LLM invoke starting...")
        trace_log("Tool execute failed", level="error")

    输出到文件:
        2026-05-05 10:00:00 | INFO     | AutoWeb | create_llm:38 | LLM invoke starting...
    """
    caller_frame = inspect.currentframe()
    # stacklevel=1 是 trace_log 本身, 2 是调用者
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
    文件日志格式: [function_name:line] -> ENTER / <- EXIT (elapsed)

    Usage:
        @log_call(level="info")
        def my_function():
            ...
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
    保存代码执行日志到 logs/code_log/ 目录

    Args:
        code: 执行的代码内容
        output: 执行输出
        is_error: 是否为错误日志
        extra_info: 额外信息（如 URL 变化等）

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


# ==================== 全局单例 ====================

logger = AutoWebLogger()
