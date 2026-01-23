import os
import sys
import json
import traceback
from dotenv import load_dotenv

# 1. 导入核心驱动
from drivers.drission_driver import BrowserDriver
from core.graph import AutoWebGraph

# 导入配置
from config import MODEL_NAME

# 加载环境变量
load_dotenv()

def setup_agent():
    """初始化全栈 Agent"""
    print("\n>>> 正在初始化浏览器驱动...")
    # 预热浏览器，确保单例被创建
    BrowserDriver.get_browser()
    
    print(">>> 正在构建 AutoWeb 大脑 (Reflexion Graph)...")
    # 将 Driver 类注入给 Graph，方便它随时获取最新 Tab
    graph_builder = AutoWebGraph(BrowserDriver)
    app = graph_builder.compile()
    
    print(f">>> 系统就绪 (Model: {MODEL_NAME})")
    return app

def print_step_output(event):
    """
    [UI层] 美化输出图执行过程中的状态更新
    实时展示 Agent 的思考过程、工具调用结果和反思
    """
    for node_name, state_update in event.items():
        print(f"\n🔄 [Node: {node_name}] 执行完成")
        
        # Case A: Planner 节点
        if node_name == "Planner" and "plan" in state_update:
            print(f"   🧠 Plan: {state_update['plan']}")
            if state_update.get("is_complete"):
                print(f"   🏁 \033[1;32mPlanner marked task as COMPLETE.\033[0m")

        # Case B: Coder 节点
        if node_name == "Coder" and "generated_code" in state_update:
            # 只显示前100字符预览
            code_preview = state_update['generated_code'][:100].replace('\n', ' ')
            print(f"   💻 Generated Code: {code_preview}...")

        # Case C: Executor 节点
        if node_name == "Executor" and "execution_log" in state_update:
            log = state_update['execution_log']
            if "Error" in log or "Exception" in log:
                 print(f"   ❌ \033[1;31mExecution Failed\033[0m: {log[:200]}...")
            else:
                 print(f"   ✅ Execution Success: {log[:200]}...")

        # Case D: Verifier 节点
        if node_name == "Verifier":
            if "reflections" in state_update and state_update["reflections"]:
                print(f"   ❌ \033[1;31mVerification Failed\033[0m: {state_update['reflections'][0]}")
            elif "finished_steps" in state_update:
                 last_step = state_update['finished_steps'][-1] if state_update['finished_steps'] else "Unknown"
                 print(f"   ✅ \033[1;32mVerification Passed\033[0m: {last_step}")
                 if state_update.get("is_complete"):
                     print(f"   🎉 Task Fully Completed!")

def interactive_loop(app):
    """交互式主循环"""
    print("\n🤖 AutoWeb Agent (Reflexion版) 已启动 — 输入自然语言任务（输入 exit 退出）")
    print("💡 提示：输入 'qa <问题>' 可直接针对知识库提问。")
    
    while True:
        try:
            user_input = input("\n👤 User > ").strip()
            if user_input.lower() in ("exit", "quit"):
                print("👋 正在关闭浏览器资源...")
                BrowserDriver.quit()
                break
            
            if not user_input:
                continue

            # --- 特殊指令：RAG 问答 ---
            if user_input.lower().startswith("qa ") or user_input.lower().startswith("ask "):
                query = user_input.split(" ", 1)[1]
                try:
                    from rag.retriever_qa import qa_interaction
                    qa_result = qa_interaction(query)
                    print(f"\n📚 [Knowledge Base]: {qa_result}")
                except Exception as e:
                    print(f"⚠️ RAG Error: {e}")
                continue

            # --- 启动 Graph 任务 ---
            print(f"🚀 开始执行任务: {user_input}")
            
            # 构造初始状态
            initial_state = {
                "user_task": user_input,
                "messages": [],         # 历史消息清空
                "loop_count": 0,        # 步数重置
                "reflections": [],      # 初始没有经验
                "error_flag": False,
                "current_url": "",
                "dom_skeleton": ""
            }
            
            # 使用 .stream() 逐步执行并获取反馈
            # recursion_limit 设置稍大一点，允许更多步数的 ReAct 循环
            try:
                for event in app.stream(initial_state, config={"recursion_limit": 50}):
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
        app = setup_agent()
        interactive_loop(app)
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        traceback.print_exc()