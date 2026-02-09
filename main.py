import os
import sys
import uuid
import traceback
from dotenv import load_dotenv

# 导入核心驱动
from drivers.drission_driver import BrowserDriver

# 导入 V2 架构构建函数
from langgraph.types import Command
from core.graph_v2 import build_graph
from langgraph.checkpoint.memory import MemorySaver

# 导入配置和依赖
from config import MODEL_NAME, OPENAI_API_KEY, OPENAI_BASE_URL
from langchain_openai import ChatOpenAI
from skills.observer import BrowserObserver

# 加载环境变量
load_dotenv()


def setup_agent():
    """初始化全栈 Agent (V2 Architecture)"""
    print("\n>>> 正在初始化浏览器驱动...")
    browser_instance = BrowserDriver.get_browser()

    print(">>> 正在初始化 LLM 和 Observer...")
    # 依赖注入：创建共享组件
    llm = ChatOpenAI(
        model=MODEL_NAME,
        temperature=0,
        openai_api_key=OPENAI_API_KEY,
        openai_api_base=OPENAI_BASE_URL,
        streaming=True
    )
    observer = BrowserObserver()

    print(">>> 正在构建 AutoWeb V2 大脑 (LangGraph)...")
    memory = MemorySaver()
    # 依赖注入：在构建图时通过 partial 绑定 LLM 和 Observer
    app = build_graph(checkpointer=memory, llm=llm, observer=observer)

    print(f">>> 系统就绪 (Model: {MODEL_NAME})")

    # 返回应用、浏览器和依赖对象
    return app, browser_instance, llm, observer


def print_step_output(event):
    """
    [UI层] 美化输出 V2 图执行过程中的状态更新
    """
    for node_name, updates in event.items():
        print(f"\n🔄 [Node: {node_name}] 执行完成")

        if "plan" in updates and updates['plan']:
            print(f"   🧠 Plan: {updates['plan']}")

        if "generated_code" in updates and updates['generated_code']:
            code_preview = updates['generated_code'][:100].replace('\n', ' ')
            print(f"   💻 Generated Code: {code_preview}...")

        if "execution_log" in updates and updates['execution_log']:
            log = updates['execution_log']
            if "Error" in log or "Exception" in log:
                print(
                    f"   ❌ \033[1;31mExecution Failed\033[0m: {log[:200]}...")
            else:
                print(f"   ✅ Execution Success: {log[:200]}...")

        if "finished_steps" in updates and updates['finished_steps']:
            last_step = updates['finished_steps'][-1] if updates['finished_steps'] else "Unknown"
            print(f"   ✅ \033[1;32mVerification Passed\033[0m: {last_step}")

        if "error" in updates and updates["error"]:
            print(f"   ⚠️ Error Flag Set: {updates['error']}")


def interactive_loop(app, browser_instance, llm, observer):
    """交互式主循环"""
    print("\n🤖 AutoWeb Agent (LangGraph V2) 已启动 — 输入自然语言任务（输入 exit 退出）")

    # 为当前会话生成唯一 Thread ID
    thread_id = str(uuid.uuid4())
    print(f"THREAD ID: {thread_id}")

    # LLM 和 Observer 实例已通过 partial 绑定到节点
    config = {
        "configurable": {
            "thread_id": thread_id,
            "browser": browser_instance,  # 浏览器实例保留，因为需要动态获取 latest_tab
        },
        "recursion_limit": 50
    }

    while True:
        try:
            # 检查是否有挂起的中断验证 (Human-in-the-Loop)
            snapshot = app.get_state(config)

            if snapshot.next:
                next_node = snapshot.next[0] if isinstance(
                    snapshot.next, tuple) else snapshot.next
                print(f"\n⏸️ 任务暂停于节点: {next_node}")

                # === 处理 Executor 中断（代码执行前审批）===
                if next_node == "Executor":
                    current_code = snapshot.values.get("generated_code", "")
                    if current_code:
                        print("\n📝 当前生成的代码:")
                        print("-" * 50)
                        print(
                            current_code[:500] + ("..." if len(current_code) > 500 else ""))
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
                        edit_file = "temp_code_edit.py"
                        with open(edit_file, "w", encoding="utf-8") as f:
                            f.write(current_code)
                        print(f"   📝 代码已保存到 {edit_file}")
                        print(f"   请使用编辑器修改文件，保存后按 Enter 继续...")
                        input("   [按 Enter 继续]")

                        with open(edit_file, "r", encoding="utf-8") as f:
                            edited_code = f.read()

                        if edited_code != current_code:
                            print("   ✅ 检测到代码修改，正在更新状态...")
                            # 更新状态并使用 as_node="Coder" 保持一致性
                            app.update_state(
                                config, {"generated_code": edited_code}, as_node="Coder")
                            print("   ⚡ 开始执行修改后的代码...")
                        else:
                            print("   ℹ️ 代码未修改，继续执行原代码...")

                        # [Fix] 使用 Command(goto="Executor") 强制指定下一步执行的节点
                        for event in app.stream(Command(goto="Executor"), config=config, stream_mode="updates"):
                            print_step_output(event)
                        continue

                    elif user_input.lower() in ("q", "quit", "exit"):
                        break

                    elif user_input:
                        print(f"   🔄 收到新指令，正在更新状态并重规划: {user_input}")
                        app.update_state(
                            config, {"user_task": f"{user_input} (User Feedback)"})
                        for event in app.stream(Command(goto="Executor"), config=config, stream_mode="updates"):
                            print_step_output(event)
                        continue

                # === 处理 Verifier 中断（验收结果人工覆盖）===
                # [V3 Fix] Verifier 现在跳转到 Observer，所以 next_node 是 Observer
                elif next_node == "Observer":
                    # 默认跳转目标
                    goto_node = "Observer"

                    # 检查是否有验收结果（表示刚从 Verifier 过来）
                    verification = snapshot.values.get(
                        "verification_result", {})
                    if verification:
                        is_success = verification.get("is_success", False)
                        is_done = verification.get("is_done", False)
                        summary = verification.get("summary", "")

                        if is_success:
                            print(
                                f"   ✅ Verification Passed: {summary[:100]}...")
                        else:
                            print(
                                f"   ❌ Verification Failed: {summary[:100]}...")

                        print(
                            "\n   验收选项: [Enter=接受] [s=强制成功] [f=强制失败] [d=强制完成]")
                        user_override = input("   👤 > ").strip().lower()

                        # 根据用户选择更新状态和跳转目标
                        if user_override == "s":
                            print("   ✅ 人工覆盖: 强制成功")
                            app.update_state(config, {
                                "verification_result": {},
                                "finished_steps": [summary]
                            }, as_node="Verifier")
                        elif user_override == "f":
                            print("   ❌ 人工覆盖: 强制失败")
                            app.update_state(config, {
                                "verification_result": {},
                                "reflections": [f"Step Failed (Manual): {summary}"]
                            }, as_node="Verifier")
                        elif user_override == "d":
                            print("   🎉 人工覆盖: 强制完成任务")
                            app.update_state(config, {
                                "verification_result": {},
                                "is_complete": True,
                                "finished_steps": [summary]
                            }, as_node="Verifier")
                            goto_node = "__end__"  # 任务完成，跳转到结束
                        else:
                            # Enter = 接受当前结果
                            if is_done:
                                print("   🎉 任务已完成！")
                                goto_node = "__end__"
                            # 清空 verification_result
                            app.update_state(
                                config, {"verification_result": {}}, as_node="Observer")

                    # 统一使用 Command(goto=goto_node) 跳转
                    for event in app.stream(Command(goto=goto_node), config=config, stream_mode="updates"):
                        print_step_output(event)
                    continue

                # === 处理任务完成中断 ===
                elif next_node == "__end__":
                    print("   🎉 任务完成！")
                    break

                # === 其他节点中断 ===
                else:
                    print(f"   ℹ️ 未知中断点: {next_node}，自动继续...")
                    for event in app.stream(None, config=config, stream_mode="updates"):
                        print_step_output(event)
                    continue

            # 正常的新任务输入
            user_input = input("\n👤 User > ").strip()
            if user_input.lower() in ("exit", "quit"):
                print("👋 正在关闭浏览器资源...")
                # 刷新知识库缓冲区
                try:
                    from skills.tool_rag import kb_manager
                    kb_manager.flush_and_wait(timeout=10.0)
                except Exception as e:
                    print(f"⚠️ 知识库刷新失败: {e}")
                BrowserDriver.quit()
                break

            # 新增：QA 命令 - 查询知识库
            if user_input.lower().startswith("qa "):
                # 只去掉 "qa " 前缀，完整问题传入
                question = user_input[3:].strip()
                if not question:
                    print("⚠️ 请输入问题，例如: qa 知识库里有什么数据？")
                    continue
                print(f"\n🔍 [RAG] 正在查询知识库...")
                try:
                    from rag.retriever_qa import qa_interaction
                    answer = qa_interaction(question)
                    print(f"\n📚 [RAG 回答]\n{answer}\n")
                except Exception as e:
                    print(f"❌ [RAG] 查询失败: {e}")
                continue

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
        app, browser, llm, observer = setup_agent()
        interactive_loop(app, browser, llm, observer)
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        traceback.print_exc()
    finally:
        # 确保知识库缓冲区刷新
        try:
            from skills.tool_rag import kb_manager
            kb_manager.flush_and_wait(timeout=5.0)
        except:
            pass
