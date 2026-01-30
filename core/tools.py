from typing import Type, Dict, Any, Optional
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from skills.observer import BrowserObserver
from skills.actor import BrowserActor

# 实例化全局 Observer (也可以在 Tool 初始化时传入)
_observer = BrowserObserver()

class DOMAnalysisInput(BaseModel):
    requirement: str = Field(description="用户针对当前页面的需求，通过分析需求来决定关注哪些元素")

class DOMAnalysisTool(BaseTool):
    name: str = "analyze_dom"
    description: str = "分析当前网页结构(DOM)，返回针对用户需求的元素定位建议。"
    args_schema: Type[BaseModel] = DOMAnalysisInput
    
    # 依赖注入
    browser: Any = None 

    def _run(self, requirement: str) -> str:
        if not self.browser:
            return "Error: Browser context not initialized."
            
        tab = self.browser.latest_tab
        # 捕获 DOM
        dom = _observer.capture_dom_skeleton(tab)
        # 分析
        strategy = _observer.analyze_locator_strategy(dom, requirement)
        return str(strategy)

class ClickElementInput(BaseModel):
    locator: str = Field(description="元素的定位符，例如 'text=Login' 或 css selector '#submit'")

class ClickElementTool(BaseTool):
    name: str = "click_element"
    description: str = "点击网页上的指定元素。"
    args_schema: Type[BaseModel] = ClickElementInput
    
    browser: Any = None 

    def _run(self, locator: str) -> str:
        if not self.browser:
            return "Error: Browser context not initialized."
            
        tab = self.browser.latest_tab
        actor = BrowserActor(tab)
        
        # 构造 action plan
        result = actor.perform_action({"action": "click", "locator": locator})
        return str(result)

class NavigateInput(BaseModel):
    url: str = Field(description="需要访问的目标网址")

class NavigateTool(BaseTool):
    name: str = "navigate_to"
    description: str = "导航浏览器到指定 URL。"
    args_schema: Type[BaseModel] = NavigateInput
    
    browser: Any = None 

    def _run(self, url: str) -> str:
        if not self.browser:
            return "Error: Browser context not initialized."
        
        tab = self.browser.latest_tab
        actor = BrowserActor(tab)
        actor.navigate(url)
        return f"Navigated to {url}"

def get_tools(browser_driver):
    """
    工厂函数：获取绑定了 Browser 的工具集
    """
    tools = [
        DOMAnalysisTool(browser=browser_driver),
        ClickElementTool(browser=browser_driver),
        NavigateTool(browser=browser_driver)
    ]
    return tools
