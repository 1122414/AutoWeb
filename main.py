import os
import sys
import uuid
import traceback
from dotenv import load_dotenv

# 1. 导入核心驱动
from drivers.drission_driver import BrowserDriver
# 导入 V2 架构构建函数
from core.graph_v2 import build_graph
from langgraph.checkpoint.memory import MemorySaver

# 导入配置
from config import MODEL_NAME

# 加载环境变量
load_dotenv()

def setup_agent():
    """初始化全栈 Agent (V2 Architecture)"""
    print("\n>>> 正在初始化浏览器驱动...")
    # 预热浏览器，确保单例被创建
    # 注意：在 V2 中，browser 对象将作为 configurable 资源传入，但最好保持全局单例以防多次初始化
    browser_instance = BrowserDriver.get_browser()
    
    print(">>> 正在构建 AutoWeb V2 大脑 (LangGraph)...")
    # 初始化 Checkpointer 实现会话记忆
    memory = MemorySaver()
    
    # 构建图
    app = build_graph(checkpointer=memory)
    
    print(f">>> 系统就绪 (Model: {MODEL_NAME})")
    
    return app, browser_instance

def print_step_output(event):
    """
    [UI层] 美化输出 V2 图执行过程中的状态更新
    """
    for node_name, updates in event.items():
        print(f"\n🔄 [Node: {node_name}] 执行完成")
        
        if "plan" in updates:
            print(f"   🧠 Plan: {updates['plan']}")
        
        if "generated_code" in updates:
            code_preview = updates['generated_code'][:100].replace('\n', ' ')
            print(f"   💻 Generated Code: {code_preview}...")
            
        if "execution_log" in updates:
            log = updates['execution_log']
            if "Error" in log or "Exception" in log:
                 print(f"   ❌ \033[1;31mExecution Failed\033[0m: {log[:200]}...")
            else:
                 print(f"   ✅ Execution Success: {log[:200]}...")
                 
        if "finished_steps" in updates:
             last_step = updates['finished_steps'][-1] if updates['finished_steps'] else "Unknown"
             print(f"   ✅ \033[1;32mVerification Passed\033[0m: {last_step}")
             
        if "error" in updates and updates["error"]:
             print(f"   ⚠️ Error Flag Set: {updates['error']}")

def interactive_loop(app, browser_instance):
    """交互式主循环"""
    print("\n🤖 AutoWeb Agent (LangGraph V2) 已启动 — 输入自然语言任务（输入 exit 退出）")
    
    # 为当前会话生成唯一 Thread ID
    thread_id = str(uuid.uuid4())
    print(f"THREAD ID: {thread_id}")
    
    config = {
        "configurable": {
            "thread_id": thread_id,
            "browser": browser_instance
        },
        "recursion_limit": 50
    }

    while True:
        try:
            # 检查是否有挂起的中断验证 (Human-in-the-Loop)
            # 在 Graph V2 中，interrupt_before=["Executor"] 可能导致线程暂停
            snapshot = app.get_state(config)
            
            if snapshot.next:
                 print(f"\n⏸️ 任务暂停于节点: {snapshot.next}")
                 print("   等待人工确认... (输入 'c' 或 'continue' 继续，输入 'q' 退出，输入其他内容作为新指令)")
                 user_input = input("\n👤 Admin > ").strip()
                 
                 if user_input.lower() in ("c", "continue", "yes", "y"):
                     print("   ✅ 批准执行，继续...")
                     # 恢复执行 (传入 None 作为 input)
                     for event in app.stream(None, config=config, stream_mode="updates"):
                        print_step_output(event)
                     continue
                     
                 elif user_input.lower() in ("q", "quit", "exit"):
                     break
                 
                 elif user_input:
                     print(f"   🔄 收到新指令，正在更新状态并重规划: {user_input}")
                     # 对于中断处的新指令，通常意味着修改计划或提供反馈
                     # 这里我们简单地作为新消息传入，但这需要 Graph 能处理
                     # 或者我们可以 update state
                     app.update_state(config, {"user_task": f"{user_input} (User Feedback)"})
                     # 然后继续
                     for event in app.stream(None, config=config, stream_mode="updates"):
                        print_step_output(event)
                     continue

            # 正常的新任务输入
            user_input = input("\n� User > ").strip()
            if user_input.lower() in ("exit", "quit"):
                print("👋 正在关闭浏览器资源...")
                BrowserDriver.quit()
                break
            
            if not user_input:
                continue

            print(f"🚀 开始执行任务: {user_input}")
            
            # V2 State 结构
            input_state = {
                "user_task": user_input,
                "messages": [("user", user_input)], 
                "loop_count": 0,
                "finished_steps": []
            }
            
            try:
                # stream_mode="updates" 只返回增量更新，适合 UI 展示
                for event in app.stream(input_state, config=config, stream_mode="updates"):
                    print_step_output(event)
                
                print("\n✅ 流程结束 (End of Graph)")
                
            except Exception as e:
                print(f"\n❌ 流程中断: {e}")
                traceback.print_exc()

        except KeyboardInterrupt:
            print("\n操作已取消")
            continue
        except Exception as e:
            print(f"\n❌ 未捕获异常: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    try:
        app, browser = setup_agent()
        interactive_loop(app, browser)
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        traceback.print_exc()