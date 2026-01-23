import json
import traceback
from langgraph.graph import StateGraph, END
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI

# 项目组件
from config import MODEL_NAME, OPENAI_API_KEY, OPENAI_BASE_URL
from core.state import AgentState
from skills.actor import BrowserActor
from skills.observer import BrowserObserver
from prompts.action_prompts import ACTION_CODE_GEN_PROMPT

# 引入模块化的组件
from core.router import admin_routing_logic, route_supervisor
from core.planner import PlannerAgent

class AutoWebGraph:
    """
    [Multi-Agent Core Graph]
    架构: Admin -> (Planner -> Coder -> Executor -> Verifier) -> Admin
    """
    def __init__(self, browser_driver):
        self.browser = browser_driver
        self.observer = BrowserObserver()
        
        # 初始化 LLM (Coder/Verifier 共用)
        self.llm = ChatOpenAI(
            model=MODEL_NAME,
            temperature=0,
            openai_api_key=OPENAI_API_KEY,
            openai_api_base=OPENAI_BASE_URL,
            
        )
        
        # 初始化 Planner Agent
        self.planner_agent = PlannerAgent(self.observer)
        self.tab = self.browser.get_latest_tab()

    # ================= 节点封装 =================
    
    def admin_node(self, state: AgentState):
        return admin_routing_logic(state)

    def planner_node(self, state: AgentState):
        return self.planner_agent.run(state, self.tab)

    def coder_node(self, state: AgentState):
        """[Coder] 编写代码"""
        print("\n💻 [Coder] 正在编写代码...")
        plan = state.get("plan", "")
        task = state.get("user_task", "")
        history_msgs = state["messages"][-2:]
        history_str = "\n".join([m.content for m in history_msgs if isinstance(m, AIMessage)])
        raw_dom = self.observer.capture_dom_skeleton(self.tab)
        
        # [Lazy Analysis Strategy]
        # 1. 检查是不是初始页/空白页
        current_url = self.tab.url
        is_start_page = (
            current_url == "about:blank" or 
            current_url.startswith("chrome://") or 
            current_url.startswith("data:") or
            "google.com" in current_url # 用户提到的 Google 初始页
        )
        
        # 2. 如果是初始页，或者 DOM 为空，或者 Plan 看起来只是单纯的导航（不包含交互）
        # 则跳过昂贵的视觉分析
        xpath_plan = ""
        should_analyze = True
        
        if is_start_page:
            print("   ⏩ [Coder] 当前为初始/空白页，跳过视觉分析，直接导航。")
            should_analyze = False
        elif raw_dom is None or "Empty DOM" in raw_dom:
            print("   ⏩ [Coder] DOM 为空，跳过视觉分析。")
            should_analyze = False
            
        if should_analyze: 
            xpath_plan = self.observer.analyze_locator_strategy(raw_dom, task)
        
            # 正确格式化 Base Prompt
            base_prompt = ACTION_CODE_GEN_PROMPT.format(
                xpath_plan = xpath_plan,
                requirement = task
            )
            
            prompt = f"""
            {base_prompt}
            
            【Planner 的执行计划】
            {plan}
            
            【最近的反馈/错误信息】
            {history_str}
            
            请生成 Python 代码。代码必须只包含函数体内部逻辑，假设 `tab` 对象已存在。
            不要包含 `import`，不要包含 `tab = Chromium()`。
            将结果存入 `results` 列表。
            """
        else:
            # 轻量级 Prompt: 仅用于生成导航代码
            prompt = f"""
            【用户任务】
            {task}
            
            【Planner 计划】
            {plan}
            
            当前无需页面交互（或者是初始空白页）。
            请直接输出跳转到目标 URL 的 DrissionPage 代码。
            
            Example:
            tab.get("https://www.baidu.com")
            """
        response = self.llm.invoke([HumanMessage(content=prompt)])
        
        content = response.content
        code = ""
        if "```python" in content:
            code = content.split("```python")[1].split("```")[0].strip()
        elif "```" in content:
            code = content.split("```")[1].split("```")[0].strip()
        else:
            code = content 
        return {"messages": [AIMessage(content=f"【代码生成】\n{response}")], "generated_code": code}

    def executor_node(self, state: AgentState):
        """[Executor] 执行代码"""
        print("\n⚡ [Executor] 正在执行代码...")
        code = state.get("generated_code", "")
        tab = self.browser.get_latest_tab()
        actor = BrowserActor(tab)
        context = {"goal": state["user_task"]}
        
        try:
            print(code)
            exec_results = actor.execute_python_strategy(code, context)
            log = f"Execution Results: {json.dumps(exec_results, ensure_ascii=False, default=str)}"
            print(f"   -> {log}")
            return {"messages": [AIMessage(content=f"【执行报告】\n{log}")], "execution_log": log}
        except Exception as e:
            error_msg = f"Runtime Error: {str(e)}\n{traceback.format_exc()}"
            print(f"   ❌ Error: {error_msg}")
            return {"messages": [AIMessage(content=f"【执行报告】\n执行出错: {error_msg}")], "execution_log": error_msg}

    def verifier_node(self, state: AgentState):
        """[Verifier] 验收结果 (Iterative)"""
        print("\n🔍 [Verifier] 正在验收...")
        
        log = state.get("execution_log", "")
        task = state.get("user_task", "")
        current_plan = state.get("plan", "Unknown Plan")

        # 截断日志和 DOM 以防止 Token 溢出 (Error 400)
        # 保留最后的 2000 字符日志，通常包含报错信息
        short_log = log[-2000:] if len(log) > 2000 else log
        
        try:
            tab = self.browser.get_latest_tab()
            # 限制 DOM 长度
            current_dom = self.observer.capture_dom_skeleton(tab)[:15000]
        except:
            current_dom = "无法获取 DOM"
        
        prompt = f"""
        你是自动化测试验收员。请验证上一步的执行情况。
        
        【用户最终目标】{task}
        【当前步骤计划】{current_plan}
        【执行日志 (部分)】{short_log}
        【当前页面 DOM (精简)】{current_dom}
        
        请判断：
        1. **步骤执行情况**: 
           - 检查【执行日志】是否有报错 (Runtime Error)。无报错通常意味着代码运行成功。
           - 检查页面是否发生了预期变化 (如 URL 变更、新元素出现)。
           - 注意：有些步骤 (如 "等待页面加载") 可能不会产生明显的 DOM 变化，只要没报错就算成功。
           
        2. **总任务进度**: 
           - 只有当用户要求的最终结果 (如文件保存、数据抓取完毕) 明确发生时，才算完成。
           - 简单的翻页或点击不代表任务结束。
        
        请严格按以下格式回复：
        Status: [STEP_SUCCESS | STEP_FAIL]
        TaskDone: [YES | NO]
        Summary: [一句话描述实际发生了什么]
        Reasoning: [你的判断理由，必须引用日志或 DOM 证据]
        
        Example 1 (Success):
        Status: STEP_SUCCESS
        TaskDone: NO
        Summary: 成功点击了搜索按钮，页面跳转至 "/s?wd=..."。
        Reasoning: 日志无报错，且 URL 已变更。
        
        Example 2 (Fail):
        Status: STEP_FAIL
        TaskDone: NO
        Summary: 无法找到元素 "btn-login"。
        Reasoning: 执行日志显示 ElementNotFound Error。
        """
        response = self.llm.invoke([HumanMessage(content=prompt)])
        content = response.content
        
        # 解析结果
        is_step_success = "Status: STEP_SUCCESS" in content
        is_task_done = "TaskDone: YES" in content
        
        # 提取 Summary
        summary = "Executed a step."
        for line in content.split("\n"):
            if line.startswith("Summary:"):
                summary = line.replace("Summary:", "").strip()
                break
        
        updates = {
            "messages": [response],
            "is_complete": False
        }
        
        if is_step_success:
            # 步骤成功：记录到 finished_steps
            existing_steps = state.get("finished_steps", [])
            updates["finished_steps"] = existing_steps + [summary]
            
            if is_task_done:
                updates["is_complete"] = True
                print(f"   ✅ [Verifier] 步骤成功，且任务完成！")
            else:
                print(f"   ✅ [Verifier] 步骤成功，继续下一步...")
        else:
            # 步骤失败：记录 Reflection
            print(f"   ❌ [Verifier] 步骤失败，需重试/重新规划。")
            updates["reflections"] = [f"Step Failed: {current_plan}. Reason: {content}"]
            
        return updates

    # ================= 编译图 =================
    
    def compile(self):
        workflow = StateGraph(AgentState)
        
        workflow.add_node("Admin", self.admin_node)
        workflow.add_node("Planner", self.planner_node)
        workflow.add_node("Coder", self.coder_node)
        workflow.add_node("Executor", self.executor_node)
        workflow.add_node("Verifier", self.verifier_node)
        
        workflow.set_entry_point("Admin")
        
        workflow.add_conditional_edges(
            "Admin",
            route_supervisor, # 使用 router.py 中的纯函数
            {
                "Planner": "Planner",
                "Coder": "Coder",
                "Executor": "Executor",
                "Verifier": "Verifier",
                "FINISH": END
            }
        )
        
        workflow.add_edge("Planner", "Admin")
        workflow.add_edge("Coder", "Admin")
        workflow.add_edge("Executor", "Admin")
        workflow.add_edge("Verifier", "Admin")
        
        return workflow.compile()