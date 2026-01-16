import os
import sys
from typing import Optional
from DrissionPage import Chromium, ChromiumOptions

# 尝试导入配置，如果未设置则使用默认值
try:
    from config import (
        HEADLESS_MODE, 
        BROWSER_USER_DATA_DIR, 
        BROWSER_ARGS
    )
except ImportError:
    # 默认兜底配置
    HEADLESS_MODE = False
    BROWSER_USER_DATA_DIR = "./browser_data"
    BROWSER_ARGS = ['--no-sandbox', '--disable-gpu']

class BrowserDriver:
    """
    [底层驱动] 浏览器实例管理器 (Singleton Pattern)
    负责：浏览器的初始化、配置、生命周期管理
    
    特点：
    - 全局单例：避免重复启动浏览器
    - 状态持久化：自动保存 Cookies 和 LocalStorage 到 browser_data 目录
    - 端口隔离：自动分配端口，支持多 Agent 并行
    """
    _instance: Optional[Chromium] = None

    @classmethod
    def get_browser(cls) -> Chromium:
        """
        获取浏览器单例实例。如果未初始化，则自动初始化。
        """
        if cls._instance is None:
            cls._init_browser()
        return cls._instance

    @classmethod
    def _init_browser(cls):
        """
        初始化 Chromium 对象
        """
        print("🚀 [Driver] Initializing Browser Engine...")
        
        co = ChromiumOptions()
        
        # 1. 基础参数配置
        for arg in BROWSER_ARGS:
            co.set_argument(arg)
            
        # 2. 运行模式 (Headless vs GUI)
        # 调试阶段建议 False，生产环境建议 True
        if HEADLESS_MODE:
            print("   -> Mode: Headless (无头模式)")
            co.headless()
        else:
            print("   -> Mode: GUI (可视化模式)")

        # 3. 用户数据持久化 (Persistence)
        # 这是 RPA 的核心：保留登录状态、缓存等
        if BROWSER_USER_DATA_DIR:
            abs_path = os.path.abspath(BROWSER_USER_DATA_DIR)
            if not os.path.exists(abs_path):
                os.makedirs(abs_path)
            print(f"   -> User Profile: {abs_path}")
            co.set_user_data_path(abs_path)

        # 4. 端口管理
        # 自动获取可用端口，防止与现有 Chrome 冲突，也支持多进程运行
        co.auto_port()

        # 5. 实例化
        try:
            cls._instance = Chromium(addr_or_opts=co)
            
            # 设置全局超时策略 (秒)
            # base: 基础元素查找超时
            # page_load: 页面加载超时
            cls._instance.set_timeouts(base=10, page_load=30)
            
        except Exception as e:
            print(f"❌ [Driver] Failed to launch browser: {e}")
            raise e

    @classmethod
    def get_latest_tab(cls):
        """获取当前活跃的标签页 (Latest Tab)"""
        return cls.get_browser().latest_tab

    @classmethod
    def new_tab(cls, url: str = None):
        """创建新标签页"""
        return cls.get_browser().new_tab(url)

    @classmethod
    def close_current_tab(cls):
        """关闭当前标签页"""
        tab = cls.get_latest_tab()
        tab.close()

    @classmethod
    def quit(cls):
        """
        彻底关闭浏览器进程
        通常在程序退出或 Graph 结束时调用
        """
        if cls._instance:
            print("🛑 [Driver] Quitting Browser...")
            try:
                cls._instance.quit()
            except Exception as e:
                print(f"⚠️ [Driver] Error during quit: {e}")
            finally:
                cls._instance = None