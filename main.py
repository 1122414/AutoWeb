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
                 
                 # 显示当前生成的代码（如果有）
                 current_code = snapshot.values.get("generated_code", "")
                 if current_code:
                     print("\n📝 当前生成的代码:")
                     print("-" * 50)
                     print(current_code[:500] + ("..." if len(current_code) > 500 else ""))
                     print("-" * 50)
                 
                 print("\n   命令选项:")
                 print("   'c' 或 'continue' - 批准执行")
                 print("   'e' 或 'edit'     - 编辑代码后执行")
                 print("   'q' 或 'quit'     - 退出")
                 print("   其他内容          - 作为新指令")
                 user_input = input("\n👤 Admin > ").strip()
                 
                 if user_input.lower() in ("c", "continue", "yes", "y"):
                     print("   ✅ 批准执行，继续...")
                     for event in app.stream(None, config=config, stream_mode="updates"):
                        print_step_output(event)
                     continue
                 
                 elif user_input.lower() in ("e", "edit"):
                     # 将代码写入临时文件供用户编辑
                     edit_file = "temp_code_edit.py"
                     with open(edit_file, "w", encoding="utf-8") as f:
                         f.write(current_code)
                     print(f"   📝 代码已保存到 {edit_file}")
                     print(f"   请使用编辑器修改文件，保存后按 Enter 继续...")
                     input("   [按 Enter 继续]")
                     
                     # 读取修改后的代码
                     with open(edit_file, "r", encoding="utf-8") as f:
                         edited_code = f.read()
                     
                     if edited_code != current_code:
                         print("   ✅ 检测到代码修改，正在更新状态...")
                         # 使用 as_node="Coder" 来保留中断点，让 Executor 继续执行
                         app.update_state(config, {"generated_code": edited_code}, as_node="Coder")
                         print("   ⚡ 开始执行修改后的代码...")
                     else:
                         print("   ℹ️ 代码未修改，继续执行原代码...")
                     
                     # 继续执行（从 Executor 恢复）
                     has_output = False
                     for event in app.stream(None, config=config, stream_mode="updates"):
                         has_output = True
                         print_step_output(event)
                     
                     if not has_output:
                         print("   ⚠️ 没有执行输出，正在重新触发执行...")
                         # 如果没有输出，可能需要手动触发
                         for event in app.stream({"generated_code": edited_code}, config=config, stream_mode="updates"):
                             print_step_output(event)
                     
                 elif user_input.lower() in ("q", "quit", "exit"):
                     break
                 
                 elif user_input:
                     print(f"   🔄 收到新指令，正在更新状态并重规划: {user_input}")
                     app.update_state(config, {"user_task": f"{user_input} (User Feedback)"})
                     for event in app.stream(None, config=config, stream_mode="updates"):
                        print_step_output(event)
                     continue

            # 正常的新任务输入
            user_input = input("\n👤 User > ").strip()
            if user_input.lower() in ("exit", "quit"):
                print("👋 正在关闭浏览器资源...")
                BrowserDriver.quit()
                break
            
            # 新增：重置会话命令
            if user_input.lower() in ("new", "reset"):
                thread_id = str(uuid.uuid4())
                config["configurable"]["thread_id"] = thread_id
                print(f"🆕 新会话已创建: {thread_id[:8]}...")
                print("   历史已清空，可以开始新任务。")
                continue
            
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