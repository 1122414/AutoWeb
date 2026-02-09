# ==============================================================================
# AutoWeb 统一日志模块
# ==============================================================================
# 功能：
# - 系统运行日志: logs/sys_log/autoweb_YYYYMMDD.log (按天轮转)
# - 代码执行日志: logs/code_log/exec_YYYYMMDD_HHMMSS.log (单次执行)
# - 同时输出到控制台和文件
# ==============================================================================

import os
import logging
import time
from logging.handlers import TimedRotatingFileHandler
from typing import Optional

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
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
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
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    prefix = "error" if is_error else "exec"
    log_file = os.path.join(CODE_LOG_DIR, f"{prefix}_{timestamp}.log")
    
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
