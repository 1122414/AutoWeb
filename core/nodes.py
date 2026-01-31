import json
import traceback
from typing import Literal, Union
from langchain_core.messages import HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from core.state_v2 import AgentState
from skills.observer import BrowserObserver
from skills.actor import BrowserActor
from prompts.action_prompts import ACTION_CODE_GEN_PROMPT
from prompts.planner_prompts import PLANNER_START_PROMPT, PLANNER_STEP_PROMPT, PLANNER_CONTINUE_PROMPT
from config import MODEL_NAME, OPENAI_API_KEY, OPENAI_BASE_URL

# 初始化共享组件
_llm = ChatOpenAI(
    model=MODEL_NAME,
    temperature=0,
    openai_api_key=OPENAI_API_KEY,
    openai_api_base=OPENAI_BASE_URL,
    streaming=True
)
_observer = BrowserObserver()

def _get_tab(config: RunnableConfig):
    browser = config["configurable"].get("browser")
    if not browser:
        # 在测试或特殊模式下可能没有 browser，这种情况下不应该 crash 除非节点必须使用它
        # 这里我们更友好地提示
        # raise ValueError("Browser instance not found in config")
        pass
    return browser.latest_tab if browser else None

def error_handler_node(state: AgentState, config: RunnableConfig) -> Command[Literal["Planner", "__end__"]]:
    """
    [ErrorHandler] 全局错误处理与回退
    当其他节点发生不可恢复的错误时跳转至此
    """
    print("\n🚑 [ErrorHandler] 检测到严重错误，正在尝试恢复...")
    
    error_msg = state.get("error", "Unknown Error")
    reflections = state.get("reflections", [])
    
    # 构建回退策略
    prompt = f"""
    系统在执行过程中遇到严重错误。
    【错误信息】{error_msg}
    【已尝试的反思】{reflections[-1] if reflections else 'None'}
    
    请分析是否可以重试或必须终止任务。
    如果可以重试，请给出建议。
    如果必须终止，请说明原因。
    
    Status: [RETRY | TERMINATE]
    Strategy: [策略描述]
    """
    
    response = _llm.invoke([HumanMessage(content=prompt)])
    content = response.content
    
    is_terminate = "Status: TERMINATE" in content
    
    updates = {
        "messages": [AIMessage(content=f"【系统故障】正在恢复...\n{content}")],
        # 清除错误标志，以便重试
        "error": None
    }
    
    if is_terminate:
        print("   ❌ ErrHandler: 决定终止任务。")
        updates["is_complete"] = True # 虽然失败了，但也算结束
        return Command(update=updates, goto="__end__")
    else:
        print("   🔄 ErrHandler: 尝试回退到 Planner 进行重规划。")
        return Command(update=updates, goto="Planner")


def planner_node(state: AgentState, config: RunnableConfig) -> Command[Literal["Coder", "__end__"]]:
    """[Planner] 负责分析环境并制定下一步计划"""
    print("\n🧠 [Planner] 正在制定计划...")
    tab = _get_tab(config)
    
    task = state["user_task"]
    loop_count = state.get("loop_count", 0)
    finished_steps = state.get("finished_steps", [])
    
    # 0. 检测当前页面状态，决定使用哪个 Prompt
    current_url = tab.url if tab else ""
    is_blank = not current_url or current_url.startswith(("about:", "data:", "chrome://"))
    is_google_home = "google.com" in current_url and "/search" not in current_url
    is_initial_page = is_blank or is_google_home
    
    # 0.1 初始启动（空白页/Google首页）
    if loop_count == 0 and is_initial_page:
        print("   ⏩ [Planner] 初始启动，跳过 DOM 分析，直接生成导航计划。")
        prompt = PLANNER_START_PROMPT.format(task=task)
        response = _llm.invoke([HumanMessage(content=prompt)])
        
        return Command(
            update={
                "messages": [response],
                "plan": response.content,
                "dom_skeleton": "(Start Page - Empty)",
                "loop_count": loop_count + 1,
                "is_complete": False
            },
            goto="Coder"
        )
    
    # 0.2 新任务但在已有页面上（任务连续性）
    if loop_count == 0 and not is_initial_page:
        print(f"   🔄 [Planner] 检测到已有页面: {current_url[:50]}..., 使用 CONTINUE Prompt。")
        finished_steps_str = "\n".join([f"- {s}" for s in finished_steps]) if finished_steps else "(无历史步骤)"
        prompt = PLANNER_CONTINUE_PROMPT.format(
            task=task,
            current_url=current_url,
            finished_steps_str=finished_steps_str
        )
        response = _llm.invoke([HumanMessage(content=prompt)])
        
        return Command(
            update={
                "messages": [response],
                "plan": response.content,
                "current_url": current_url,
                "loop_count": loop_count + 1,
                "is_complete": False
            },
            goto="Coder"
        )

    # 1. 环境感知
    try:
        dom = _observer.capture_dom_skeleton(tab)[:50000] 
        finished_steps = state.get("finished_steps", [])

        # [Optim] DOM Redundancy Check
        import hashlib
        current_dom_hash = hashlib.md5(dom.encode()).hexdigest()
        previous_dom_hash = state.get("dom_hash", "")
        
        # 获取历史累积的策略列表
        accumulated_strategies = state.get("locator_suggestions", [])
        
        # 只有当 DOM 发生变化时，才进行视觉分析
        should_analyze = (current_dom_hash != previous_dom_hash)
        new_strategy_entry = None  # 本轮新分析的策略

        if should_analyze:
            print(f"   -> 正在进行视觉定位分析 (Context: {len(finished_steps)} finished steps)...")
            locator_suggestions = _observer.analyze_locator_strategy(dom, task, previous_steps=finished_steps)
            
            # [Fix] 兼容单字典返回的情况
            if isinstance(locator_suggestions, dict):
                locator_suggestions = [locator_suggestions]
            
            # 构建带上下文的策略条目
            # 从最近完成的步骤提取页面上下文
            page_context = finished_steps[-1] if finished_steps else "初始页面"
            new_strategy_entry = {
                "page_context": page_context,
                "url": current_url,
                "strategies": locator_suggestions
            }
            print(f"   -> 新增策略条目: {page_context[:30]}...")
        else:
            print("   -> 页面无变化，复用历史策略 (Skipping Observer Analysis)...")

        # 构建完整的策略列表字符串（包含历史 + 本轮新增）
        all_strategies = accumulated_strategies.copy() if accumulated_strategies else []
        if new_strategy_entry:
            all_strategies.append(new_strategy_entry)
        
        if all_strategies:
            suggestions_str = json.dumps(all_strategies, ensure_ascii=False, indent=2)
        else:
            suggestions_str = "无特定定位建议，请自行分析 DOM。"
    except Exception as e:
        dom = f"DOM Capture Failed: {e}"
        suggestions_str = f"视觉分析失败: {str(e)}"
        new_strategy_entry = None

    reflections = state.get("reflections", [])
    reflection_str = ""
    if reflections:
        reflection_str = "\n⚠️ **之前的失败教训 (请在规划时重点规避)**:\n" + "\n".join([f"- {r}" for r in reflections])

    finished_steps = state.get("finished_steps", [])
    finished_steps_str = "\n".join([f"- {s}" for s in finished_steps]) if finished_steps else "(无)"

    # 2. 制定计划
    # 改动：不需要再次把dom给Planner，仅把策略给他即可
    prompt = PLANNER_STEP_PROMPT.format(
        task=task,
        finished_steps_str=finished_steps_str,
        suggestions_str=suggestions_str,
        reflection_str=reflection_str
    )
    response = _llm.invoke([HumanMessage(content=prompt)])
    content = response.content
    is_finished = "【任务已完成】" in content
    
    update_dict = {
        "messages": [response],
        "plan": content,
        "dom_skeleton": dom,
        # 只追加本轮新分析的策略（Reducer 会自动累积）
        "locator_suggestions": [new_strategy_entry] if new_strategy_entry else [],
        "dom_hash": current_dom_hash, # [Optim] 保存当前 DOM Hash
        "loop_count": state.get("loop_count", 0) + 1,
        "is_complete": is_finished
    }
    
    # 3. 动态路由
    if is_finished:
        print("🏁 [Planner] 判定任务完成，流程结束。")
        return Command(update=update_dict, goto="__end__")
    else:
        return Command(update=update_dict, goto="Coder")

def coder_node(state: AgentState, config: RunnableConfig) -> Command[Literal["Executor"]]:
    """[Coder] 编写代码"""
    print("\n💻 [Coder] 正在编写代码...")
    
    plan = state.get("plan", "")
    task = state.get("user_task", "")
    
    # 获取累积的定位策略列表，序列化为 JSON 字符串
    accumulated_strategies = state.get("locator_suggestions", [])
    if accumulated_strategies:
        xpath_plan = json.dumps(accumulated_strategies, ensure_ascii=False, indent=2)
        print(f"   -> Coder 收到 {len(accumulated_strategies)} 个页面的定位策略")
    else:
        xpath_plan = "无定位策略"
    
    # 构建 Prompt
    base_prompt = ACTION_CODE_GEN_PROMPT.format(
        xpath_plan = xpath_plan,
        # user_context = task
    )
    
    prompt = f"""
⚠️ **【唯一任务】** - 你必须且只能完成以下计划，禁止做任何其他事情！
{plan}

---
{base_prompt}
"""
    response = _llm.invoke([HumanMessage(content=prompt)])
    
    # 代码提取逻辑
    content = response.content
    code = ""
    if "```python" in content:
        code = content.split("```python")[1].split("```")[0].strip()
    elif "```" in content:
        code = content.split("```")[1].split("```")[0].strip()
    else:
        code = content
        
    return Command(
        update={
            "messages": [AIMessage(content=f"【代码生成】\n{response.content}")],
            "generated_code": code
        },
        goto="Executor"
    )

def executor_node(state: AgentState, config: RunnableConfig) -> Command[Literal["Verifier", "Planner"]]:
    """[Executor] 执行代码"""
    print("\n⚡ [Executor] 正在执行代码...")
    tab = _get_tab(config)
    code = state.get("generated_code", "")
    
    actor = BrowserActor(tab)
    
    try:
        # 执行代码
        exec_output = actor.execute_python_strategy(code, {"goal": state["user_task"]})
        execution_log = exec_output.get("execution_log", "")
        
        print(f"   -> Log Length: {len(execution_log)}")
        
        return Command(
            update={
                "messages": [AIMessage(content=f"【执行报告】\n{execution_log}")],
                "execution_log": execution_log
            },
            goto="Verifier"
        )
        
    except Exception as e:
        error_msg = f"Critical Execution Error: {str(e)}"
        print(f"   ❌ {error_msg}")
        traceback.print_exc()
        
        # 跳转到 ErrorHandler
        return Command(
            update={
                "messages": [AIMessage(content=f"【执行崩溃】\n{error_msg}")],
                "execution_log": error_msg,
                "error": str(e),
                "reflections": [f"Execution crashed: {str(e)}"]
            },
            goto="ErrorHandler"
        )

def verifier_node(state: AgentState, config: RunnableConfig) -> Command[Literal["Planner", "__end__"]]:
    """[Verifier] 验收并决定下一步"""
    print("\n🔍 [Verifier] 正在验收...")
    
    log = state.get("execution_log", "")
    task = state.get("user_task", "")
    current_plan = state.get("plan", "Unknown Plan")
    tab = _get_tab(config)
    current_url = tab.url if tab else ""

    # 1. 快速失败检查（仅致命错误）
    fatal_keywords = ["Runtime Error:", "Traceback", "ElementNotFound", "TimeoutException", "Execution Failed", "Critical"]
    for kw in fatal_keywords:
        if kw in log:
            print(f"⚡ [Verifier] Deterministic Fail: {kw}")
            return Command(
                update={
                    "messages": [AIMessage(content=f"Status: STEP_FAIL ({kw})")],
                    "reflections": [f"Step Failed: {current_plan}. Error: {kw}"],
                    "is_complete": False
                },
                goto="Planner"
            )

    # 2. LLM 验收（优化 Prompt）
    prompt = f"""
    你是自动化测试验收员。请根据以下信息判断步骤是否成功。
    
    【用户目标】{task}
    【当前计划】{current_plan}
    【当前 URL】{current_url}
    【执行日志】{log[-2000:]}
    
    【验收原则】
    1. **Warning 不算失败**: "Warning:"、"Failed to wait"、"没有等到新标签页" 等提示只是警告，不影响整体成功
    2. **关注操作结果**: 判断计划中的核心操作是否执行成功，忽略无关紧要的副作用
    3. **宽容对待非致命错误**: 只有导致任务无法继续的错误才算失败
    
    格式:
    Status: [STEP_SUCCESS | STEP_FAIL]
    TaskDone: [YES | NO]
    Summary: [简短描述]
    """
    response = _llm.invoke([HumanMessage(content=prompt)])
    content = response.content
    
    is_success = "Status: STEP_SUCCESS" in content
    is_done = "TaskDone: YES" in content
    
    summary = "Step executed."
    for line in content.split("\n"):
        if line.startswith("Summary:"):
            summary = line.replace("Summary:", "").strip()
    
    # 3. 显示验收结果，允许人工覆盖
    print(f"\n📋 [Verifier] LLM 判定:")
    print(f"   Status: {'SUCCESS' if is_success else 'FAIL'}")
    print(f"   TaskDone: {'YES' if is_done else 'NO'}")
    print(f"   Summary: {summary[:100]}")
    
    # 人工覆盖选项
    print("\n   验收选项: [Enter=接受] [s=强制成功] [f=强制失败] [d=强制完成]")
    try:
        user_override = input("   👤 > ").strip().lower()
        if user_override == "s":
            print("   ✅ 人工覆盖: 强制成功")
            is_success = True
            is_done = False
        elif user_override == "f":
            print("   ❌ 人工覆盖: 强制失败")
            is_success = False
        elif user_override == "d":
            print("   🎉 人工覆盖: 强制完成")
            is_success = True
            is_done = True
    except:
        pass  # 非交互环境，跳过
            
    updates = {
        "messages": [response],
        "is_complete": is_done,
        "current_url": current_url
    }
    
    if is_success:
        updates["finished_steps"] = [summary]
        if is_done:
            print("   🎉 Task Done!")
            return Command(update=updates, goto="__end__")
        else:
            print("   🔄 Step OK, next...")
            return Command(update=updates, goto="Planner")
    else:
        print("   ❌ Step Failed, retrying...")
        updates["reflections"] = [f"Step Failed: {summary}"]
        return Command(update=updates, goto="Planner")

