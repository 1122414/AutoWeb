import os
import sys
import uuid
import re
import traceback
from datetime import datetime
from typing import Any, Dict, List

# 强制设置终端输出编码为 UTF-8 (兼容 Windows)
if sys.platform.startswith('win'):
    import io
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer,
        encoding='utf-8',
        line_buffering=True,
        write_through=True,
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer,
        encoding='utf-8',
        line_buffering=True,
        write_through=True,
    )

# 导入核心驱动
from drivers.drission_driver import BrowserDriver

# 导入 V2 架构构建函数
from langgraph.types import Command
from core.graph_v2 import build_graph
from langgraph.checkpoint.memory import MemorySaver

# 导入配置和依赖 (统一从 config 加载)
from config import *
from core.llm_factory import create_llm
from skills.observer import BrowserObserver


def setup_agent():
    """初始化全栈 Agent (V2 Architecture)"""
    print("\n>>> 正在初始化浏览器驱动...")
    browser_instance = BrowserDriver.get_browser()

    print(">>> 正在初始化 LLM 和 Observer...")
    # 依赖注入：为各节点创建独立 LLM（相同配置会自动复用同一实例）
    llm = create_llm(MODEL_NAME, OPENAI_API_KEY, OPENAI_BASE_URL)
    coder_llm = create_llm(CODER_MODEL_NAME, CODER_API_KEY, CODER_BASE_URL)
    planner_llm = create_llm(
        PLANNER_MODEL_NAME, PLANNER_API_KEY, PLANNER_BASE_URL)
    verifier_llm = create_llm(
        VERIFIER_MODEL_NAME, VERIFIER_API_KEY, VERIFIER_BASE_URL)

    observer = BrowserObserver()

    print(">>> 正在构建 AutoWeb V2 大脑 (LangGraph)...")
    memory = MemorySaver()
    # 依赖注入：在构建图时通过 partial 绑定各节点独立 LLM
    app = build_graph(
        checkpointer=memory, llm=llm, observer=observer,
        coder_llm=coder_llm, planner_llm=planner_llm, verifier_llm=verifier_llm
    )

    # 打印系统配置信息
    print(f">>> 系统就绪")
    print(f"    【模型配置】")
    print(f"    Default : {MODEL_NAME}")
    print(f"    Coder   : {CODER_MODEL_NAME}")
    print(f"    Planner : {PLANNER_MODEL_NAME}")
    print(f"    Verifier: {VERIFIER_MODEL_NAME}")
    print(f"    Observer: {OBSERVER_MODEL_NAME}")
    print(f"    【功能开关】")
    print(
        f"    Code Cache: {'✅ Enabled' if CODE_CACHE_ENABLED else '❌ Disabled'}")
    print(
        f"    DOM Cache : {'✅ Enabled' if DOM_CACHE_ENABLED else '❌ Disabled'}")

    # 返回应用、浏览器和依赖对象
    return app, browser_instance, llm, observer


def print_step_output(event):
    """
    [UI层] 美化输出 V2 图执行过程中的状态更新
    """
    for node_name, updates in event.items():
        if not isinstance(updates, dict):
            continue
        print(f"\n🔄 [Node: {node_name}] 执行完成")

        if "plan" in updates and updates['plan']:
            print(f"   🧠 Plan: {updates['plan']}")

        if "generated_code" in updates and updates['generated_code']:
            code_preview = updates['generated_code'][:100].replace('\n', ' ')
            print(f"   💻 Generated Code: {code_preview}...")

        verification = updates.get("verification_result") or {}
        if verification:
            is_success = bool(verification.get("is_success", False))
            summary = str(verification.get("summary", "") or "")
            source = str(verification.get("source", "") or "")
            scope = str(verification.get("failure_scope", "") or "")
            status_txt = "SUCCESS" if is_success else "FAIL"
            print(
                f"   [{'OK' if is_success else 'FAIL'}] Verification[{status_txt}]"
                f"{' [' + source + ']' if source else ''}: {summary[:200]}"
            )
            if not is_success and scope:
                print(f"   -> failure_scope: {scope}")

        if "execution_log" in updates and updates['execution_log']:
            log = updates['execution_log']
            if "Error" in log or "Exception" in log:
                print(
                    f"   ❌ \033[1;31m执行失败\033[0m: {log[:200]}...")
            else:
                print(f"   ✅ 执行成功: {log[:200]}...")

        if "finished_steps" in updates and updates['finished_steps']:
            last_step = updates['finished_steps'][-1] if updates['finished_steps'] else "Unknown"
            print(f"   ✅ \033[1;32m验证通过\033[0m: {last_step}")

        if "error" in updates and updates["error"]:
            print(f"   ⚠️ 错误标识: {updates['error']}")


def _normalize_hitl_mode(mode: str) -> str:
    text = (mode or "").strip().lower()
    if text in ("on", "review_all", "review-all", "all", "1", "true"):
        return "review_all"
    return "off"


def _set_hitl_mode(app, config, mode: str) -> str:
    normalized = _normalize_hitl_mode(mode)
    try:
        app.update_state(config, {"hitl_mode": normalized})
    except Exception:
        pass
    return normalized


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _build_manual_verification_result(
    *,
    is_success: bool,
    summary: str,
    is_done: bool = False,
    failure_scope: str = "local",
    failed_action: str = "",
    failed_locator: str = "",
    evidence: str = "",
    fix_hint: str = "",
) -> Dict[str, Any]:
    return {
        "is_success": bool(is_success),
        "is_done": bool(is_done) if is_success else False,
        "summary": str(summary or "Manual review result"),
        "source": "manual",
        "failure_scope": "global" if str(failure_scope).lower() == "global" else "local",
        "failed_action": str(failed_action or ""),
        "failed_locator": str(failed_locator or ""),
        "evidence": str(evidence or ""),
        "fix_hint": str(fix_hint or ""),
    }


def _detect_executor_forced_reasons(values: dict) -> List[str]:
    reasons: List[str] = []
    code = values.get("generated_code", "") or ""
    if not code:
        return reasons

    if HITL_FORCE_EXEC_HIGH_RISK:
        for label, pattern in HITL_EXEC_HIGH_RISK_RULES:
            if re.search(pattern, code, re.IGNORECASE):
                reasons.append(f"High-risk code action: {label}")
                break

    step_fail_count = _safe_int(values.get("_step_fail_count", 0))
    if HITL_FORCE_STEP_FAIL_THRESHOLD > 0 and step_fail_count >= HITL_FORCE_STEP_FAIL_THRESHOLD:
        reasons.append(
            f"Consecutive step failures too high: {step_fail_count}")

    if HITL_FORCE_EXEC_IRREVERSIBLE:
        for label, pattern in HITL_EXEC_IRREVERSIBLE_RULES:
            if re.search(pattern, code, re.IGNORECASE):
                reasons.append(f"Irreversible page action: {label}")
                break

    return reasons


def _detect_verifier_forced_reasons(values: dict) -> List[str]:
    reasons: List[str] = []
    verification = values.get("verification_result") or {}
    if not verification:
        return reasons

    is_success = bool(verification.get("is_success", False))
    summary = str(verification.get("summary", "") or "")
    execution_log = str(values.get("execution_log", "") or "")

    messages = values.get("messages", []) or []
    last_content = ""
    if messages:
        last_msg = messages[-1]
        if isinstance(last_msg, tuple) and len(last_msg) > 1:
            last_content = str(last_msg[1])
        else:
            last_content = str(getattr(last_msg, "content", ""))
    verifier_text = f"{summary}\n{last_content}"

    if HITL_FORCE_VERIFIER_LOW_CONF and re.search(HITL_VERIFIER_LOW_CONF_REGEX, verifier_text, re.IGNORECASE):
        reasons.append("验证器置信度低")

    if HITL_FORCE_VERIFIER_LOG_CONFLICT:
        log_l = execution_log.lower()
        has_fatal = any(
            k.lower() in log_l for k in HITL_VERIFIER_FATAL_KEYWORDS)
        has_success = any(
            k.lower() in log_l for k in HITL_VERIFIER_SUCCESS_KEYWORDS)

        if is_success and has_fatal:
            reasons.append("验证器/日志冲突：验收结果为成功但日志包含错误关键词")
        if (not is_success) and has_success and not has_fatal:
            reasons.append("验证器/日志冲突：验收结果为失败但日志包含成功关键词")

    return reasons


def interactive_loop(app, browser_instance, llm, observer):
    """交互式主循环"""
    print("\n🤖 AutoWeb Agent (LangGraph V2) 已启动 — 输入自然语言任务（输入 exit 退出）")

    # 为当前会话生成唯一 Thread ID
    print(f"THREAD ID: {str(uuid.uuid4())}")

    # LLM 和 Observer 实例已通过 partial 绑定到节点
    config = {
        "configurable": {
            "thread_id": str(uuid.uuid4()),
            "browser": browser_instance,  # 浏览器实例保留，因为需要动态获取 latest_tab
        },
        "recursion_limit": 50
    }

    session_hitl_mode = _normalize_hitl_mode(HITL_MODE_DEFAULT)
    print(
        f"HITL 模式: {session_hitl_mode} "
        "(off=仅强制触发点中断; review_all=每一步都需要人工审核)"
    )

    while True:
        try:
            # (Human-in-the-Loop)
            snapshot = app.get_state(config)

            if snapshot.next:
                next_node = snapshot.next[0] if isinstance(
                    snapshot.next, tuple) else snapshot.next
                active_mode = _normalize_hitl_mode(session_hitl_mode)
                print(f"\n⏸️ 任务暂停于节点: {next_node}")

                # === 处理 Executor 中断（代码执行前审批）===
                if next_node == "Executor":
                    forced_reasons = _detect_executor_forced_reasons(
                        snapshot.values)
                    needs_review = (active_mode == "review_all") or bool(
                        forced_reasons)
                    if not needs_review:
                        print("   🔔 HITL 已关闭且未触发强制审核点，自动继续...")
                        for event in app.stream(None, config=config, stream_mode="updates"):
                            print_step_output(event)
                        continue

                    current_code = snapshot.values.get("generated_code", "")
                    if current_code:
                        print("\n📜 当前生成的代码:")
                        print("-" * 50)
                        print(
                            current_code[:500] + ("..." if len(current_code) > 500 else ""))
                        print("-" * 50)

                    print("\n   命令选项:")
                    print("   'c' 或 'continue' - 批准执行")
                    print("   'e' 或 'edit'     - 编辑代码后执行")
                    print("   'hitl on/off'     - 切换 HITL 模式")
                    print("   'q' 或 'quit'     - 退出")
                    print("   其他内容          - 作为新指令")
                    if active_mode == "review_all":
                        print("   [HITL] 当前为 review_all 模式，需要手动批准")
                    if forced_reasons:
                        print("   [HITL] 触发强制审核原因:")
                        for idx, reason in enumerate(forced_reasons, 1):
                            print(f"      {idx}. {reason}")
                    user_input = input("\n👤 Admin > ").strip()

                    if user_input.lower() in ("hitl on", "hitl off"):
                        session_hitl_mode = _set_hitl_mode(
                            app, config, "review_all" if user_input.lower() == "hitl on" else "off"
                        )
                        print(f"   ⚙️ HITL 模式已切换为: {session_hitl_mode}")
                        continue

                    if user_input.lower() in ("c", "continue", "yes", "y"):
                        print("   ✅ 批准执行，继续...")
                        for event in app.stream(None, config=config, stream_mode="updates"):
                            print_step_output(event)
                        continue

                    elif user_input.lower() in ("e", "edit"):
                        edit_file = "temp_code_edit.py"
                        with open(edit_file, "w", encoding="utf-8") as f:
                            f.write(current_code)
                        print(f"   📜 代码已保存到 {edit_file}")
                        print(f"   请使用编辑器修改文件，保存后按 Enter 继续...")
                        input("   [按 Enter 继续]")

                        with open(edit_file, "r", encoding="utf-8") as f:
                            edited_code = f.read()

                        if edited_code != current_code:
                            print("   ✅ 检测到代码修改，正在更新状态...")
                            # 更新状态并使用 as_node="Coder" 保持一致性
                            app.update_state(
                                config, {"generated_code": edited_code}, as_node="Coder")
                            print("   🚀 开始执行修改后的代码...")
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
                        forced_reasons = _detect_verifier_forced_reasons(
                            snapshot.values)
                        needs_review = (active_mode == "review_all") or bool(
                            forced_reasons)

                        if is_success:
                            print(
                                f"   ✅ 验证通过: {summary[:100]}...")
                        else:
                            print(
                                f"   ❌ 验证失败: {summary[:100]}...")
                        if active_mode == "review_all":
                            print("   [HITL] 当前为 review_all 模式，需要手动批准")
                        if forced_reasons:
                            print("   [HITL] 触发强制审核原因:")
                            for idx, reason in enumerate(forced_reasons, 1):
                                print(f"      {idx}. {reason}")
                        if not needs_review:
                            print("   🔔 HITL 已关闭且未触发强制审核点，自动接受验证结果")
                            if is_done:
                                goto_node = "__end__"
                            for event in app.stream(Command(goto=goto_node), config=config, stream_mode="updates"):
                                print_step_output(event)
                            continue

                        print(
                            "\n   验收选项: [Enter=接受] [s=强制成功] [f=强制失败] [d=强制完成]")
                        print(
                            "   或输入任意文字作为反馈，Planner 将据此重新规划")
                        print("   也可输入: hitl on / hitl off")
                        user_override = input("   👤 > ").strip()

                        if user_override.lower() in ("hitl on", "hitl off"):
                            session_hitl_mode = _set_hitl_mode(
                                app, config, "review_all" if user_override.lower() == "hitl on" else "off"
                            )
                            print(f"   ⚙️ HITL 模式已切换为: {session_hitl_mode}")
                            continue

                        # 根据用户选择更新状态和跳转目标
                        if user_override.lower() == "s":
                            print("   ✅ 人工覆盖: 强制成功")
                            app.update_state(config, {
                                "verification_result": _build_manual_verification_result(
                                    is_success=True,
                                    is_done=False,
                                    summary=summary,
                                    failure_scope="local",
                                    failed_action=snapshot.values.get(
                                        "plan", ""),
                                    evidence="manual_override_success",
                                    fix_hint="manual accepted success, continue",
                                ),
                                "finished_steps": [summary]
                            }, as_node="Verifier")
                        elif user_override.lower() == "f":
                            print("   ❌ 人工覆盖: 强制失败")
                            app.update_state(config, {
                                "verification_result": _build_manual_verification_result(
                                    is_success=False,
                                    is_done=False,
                                    summary=f"Step Failed (Manual): {summary}",
                                    failure_scope="local",
                                    failed_action=snapshot.values.get(
                                        "plan", ""),
                                    evidence="manual_override_fail",
                                    fix_hint="manual flagged failure, fix current step",
                                ),
                                "reflections": [f"Step Failed (Manual): {summary}"]
                            }, as_node="Verifier")
                        elif user_override.lower() == "d":
                            print("   🎉 人工覆盖: 强制完成任务")
                            app.update_state(config, {
                                "verification_result": _build_manual_verification_result(
                                    is_success=True,
                                    is_done=True,
                                    summary=summary,
                                    failure_scope="global",
                                    failed_action=snapshot.values.get(
                                        "plan", ""),
                                    evidence="manual_override_done",
                                    fix_hint="manual marked task complete",
                                ),
                                "is_complete": True,
                                "finished_steps": [summary]
                            }, as_node="Verifier")
                            goto_node = "__end__"  # 任务完成，跳转到结束
                        elif user_override:
                            # 人工反馈：将用户输入注入 reflections，让 Planner 据此重新规划
                            print(f"   📜 人工反馈已注入，Planner 将据此重新规划")
                            app.update_state(config, {
                                "verification_result": _build_manual_verification_result(
                                    is_success=False,
                                    is_done=False,
                                    summary=f"{summary} | user_feedback: {user_override}",
                                    failure_scope="local",
                                    failed_action=snapshot.values.get(
                                        "plan", ""),
                                    evidence="manual_feedback",
                                    fix_hint="manual feedback requires local fix",
                                ),
                                "reflections": [f"用户反馈: {user_override}"],
                                "_cache_failed_this_round": True,  # 强制跳过缓存，走 Coder 重新生成
                            }, as_node="Verifier")
                        else:
                            # Enter = 接受当前结果
                            if is_done:
                                print("   🎉 任务已完成！")
                                goto_node = "__end__"

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
                    print(f"   ℹ️ 未知中断点 {next_node}，自动继续...")
                    for event in app.stream(None, config=config, stream_mode="updates"):
                        print_step_output(event)
                    continue

            # 正常的新任务输入
            user_input = input("\n👤 User > ").strip()
            lower_input = user_input.lower()
            if lower_input in ("exit", "quit"):
                print("👋 正在关闭浏览器资源...")
                # 刷新知识库缓冲区
                try:
                    from skills.tool_rag import kb_manager
                    kb_manager.flush_and_wait(timeout=10.0)
                except Exception as e:
                    print(f"⚠️ 知识库刷新失败: {e}")
                BrowserDriver.quit()
                break

            if lower_input in ("hitl", "hitl status"):
                print(f"HITL MODE: {session_hitl_mode}")
                continue

            if lower_input in ("hitl on", "hitl off"):
                session_hitl_mode = _set_hitl_mode(
                    app, config, "review_all" if lower_input == "hitl on" else "off"
                )
                print(f"HITL MODE -> {session_hitl_mode}")
                continue

            # 新增：QA 命令 - 查询知识库
            if lower_input.startswith("qa "):
                # 只去掉 "qa " 前缀，完整问题传入
                question = user_input[3:].strip()
                if not question:
                    print("⚠️ 请输入问题，例如: qa 知识库里有什么数据？")
                    continue
                print(f"\n🔍 [RAG] 正在查询知识库...")
                try:
                    from rag.retriever_qa import qa_interaction
                    answer = qa_interaction(question)
                    print(f"\n📖 [RAG 回答]\n{answer}\n")
                except Exception as e:
                    print(f"❌ [RAG] 查询失败: {e}")
                continue

            # 新增：重置会话命令
            if lower_input in ("new", "reset"):
                thread_id = str(uuid.uuid4())
                config["configurable"]["thread_id"] = thread_id
                print(f"🆕 新会话已创建: {thread_id[:8]}...")
                print("   历史已清空，可以开始新任务。")
                print(f"   HITL MODE 继承为: {session_hitl_mode}")
                continue

            if not user_input:
                continue

            print(f"🚀 开始执行任务: {user_input}")

            # V2 State 结构
            input_state = {
                "user_task": user_input,
                "messages": [("user", user_input)],
                "loop_count": 0,
                "finished_steps": [],
                "hitl_mode": session_hitl_mode,
                "_task_started_at": datetime.now().isoformat(),
                "_cache_failed_this_round": False,
                "_cache_hit_id": None,
                "_failed_code_cache_ids": [],
                "_failed_dom_cache_ids": [],
            }

            try:
                # stream_mode="updates" 只返回增量更新，适合 UI 展示
                for event in app.stream(input_state, config=config, stream_mode="updates"):
                    print_step_output(event)

                snapshot_after = app.get_state(config)
                next_nodes = getattr(snapshot_after, "next", None)
                values = getattr(snapshot_after, "values", {}) or {}
                if (not next_nodes) and bool(values.get("is_complete", False)):
                    print("\n✅ 流程结束 (图执行完毕)")

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
