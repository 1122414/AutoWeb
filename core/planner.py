import json
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from core.state import AgentState
from config import MODEL_NAME, OPENAI_API_KEY, OPENAI_BASE_URL
from skills.observer import BrowserObserver

class PlannerAgent:
    def __init__(self, observer: BrowserObserver):
        self.observer = observer
        self.llm = ChatOpenAI(
            model=MODEL_NAME,
            temperature=0,
            openai_api_key=OPENAI_API_KEY,
            openai_api_base=OPENAI_BASE_URL,
            
        )

    def run(self, state: AgentState, browser_tab):
        """
        执行规划逻辑
        """
        print("\n🧠 [Planner] 正在制定计划...")
        
        task = state["user_task"]
        loop_count = state.get("loop_count", 0)
        
        # [Lazy Planning Strategy]
        # 如果是第一步 (Step 0)，通常只是需要一个导航动作，不需要分析 DOM
        # 这样可以显著节省 Token 并加快启动速度
        if loop_count == 0:
            print("   ⏩ [Planner] 初始启动，跳过 DOM 分析，直接生成导航计划。")
            dom = "(Start Page - Empty)"
            suggestions_str = "(无 - 初始导航阶段)"
            prompt = f"""
            你是一个网页自动化规划专家。
            
            【用户任务】
            {task}
            
            【当前状态】
            浏览器刚启动，处于空白页/初始页。
            
            请直接制定**第一步**计划（通常是打开目标网址）。
            
            回复格式：
            【计划已生成】
            1. 打开网址 https://...
            """
            
            response = self.llm.invoke([HumanMessage(content=prompt)])
            return {
                "messages": [response],
                "plan": response.content,
                "dom_skeleton": dom,
                "loop_count": loop_count + 1,
                "is_complete": False
            }

        # 1. 感知环境 (Observer)
        try:
            # 捕获 DOM (限制长度防止 Token 溢出)
            dom = self.observer.capture_dom_skeleton(browser_tab)[:30000] 
            
            # 【适配重点】调用视觉分析，获取定位建议列表
            # 注意：这里返回的是 List[Dict]，例如 [{"locator": "#search"}, {"locator": "#btn"}]
            print("   -> 正在进行视觉定位分析...")
            locator_suggestions = self.observer.analyze_locator_strategy(dom, task)
            
            # 将列表序列化为格式化的 JSON 字符串，以便嵌入 Prompt
            if isinstance(locator_suggestions, list) and locator_suggestions:
                suggestions_str = json.dumps(locator_suggestions, ensure_ascii=False, indent=2)
            else:
                suggestions_str = "无特定定位建议，请自行分析 DOM。"
                
        except Exception as e:
            dom = f"DOM Capture Failed: {e}"
            suggestions_str = f"视觉分析失败: {str(e)}"

        reflections = state.get("reflections", [])
        
        # 2. 注入反思记忆
        reflection_str = ""
        if reflections:
            reflection_str = "\n⚠️ **之前的失败教训 (请在规划时重点规避)**:\n" + "\n".join([f"- {r}" for r in reflections])

        finished_steps = state.get("finished_steps", [])
        finished_steps_str = "\n".join([f"- {s}" for s in finished_steps]) if finished_steps else "(无)"

        # 3. 构建 Prompt
        # 我们将建议列表 explicit 地展示给 Planner
        prompt = f"""
        你是一个精通网页自动化的规划专家。目前采用【迭代式规划】模式。
        
        【用户最终目标 - 时刻牢记】
        {task}
        
        【已完成步骤】
        {finished_steps_str}
        
        【当前页面 DOM (精简)】
        {dom}
        
        【视觉辅助定位建议 (Visual Suggestions)】
        {suggestions_str}
        
        {reflection_str}
        
        请制定**下一步**的行动计划。
        
        【规划原则 - 核心铁律】
        1. **单步执行 (Atomic Step)**: 每次**只能制定 1 个步骤**。
           - ❌ 错误: "1. 点击链接 2. 等待加载" (禁止一次性吐出多步)
           - ✅ 正确: "1. 点击链接" (等待和后续操作留给下一轮)
        2. **视觉优先**: 优先使用 Suggestion 中的定位符。
        3. **目标校准**: 确保这一步是在推进【用户最终目标】。
        4. **任务终结**: 只有当目标彻底达成时，输出 "【任务已完成】"。

        回复格式要求：
        如果不结束，必须包含 "【计划已生成】" 字样，且**只有一行计划**。
        如果结束，必须包含 "【任务已完成】" 字样。
        
        Example Output 1 (Next Step):
        【计划已生成】
        1. 点击左侧导航栏的 "电影" 链接 (a[href="/vod..."])。
        
        Example Output 2 (Finished):
        【任务已完成】
        所有数据抓取完毕并已保存。
        """
        
        response = self.llm.invoke([HumanMessage(content=prompt)])
        
        is_finished = "【任务已完成】" in response.content
        
        return {
            "messages": [response],
            "plan": response.content,
            "dom_skeleton": dom,
            "locator_suggestions": suggestions_str, # [Optimization] 将感知结果存入 State
            "loop_count": state.get("loop_count", 0) + 1,
            "is_complete": is_finished
        }