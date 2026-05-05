import sys
import os
import time
from unittest.mock import MagicMock

# 将项目根目录加入 Path
sys.path.append(os.getcwd())

from core.graph_v2 import build_graph
from langgraph.checkpoint.memory import MemorySaver

def mock_browser_driver():
    """Mock 浏览器驱动"""
    mock_browser = MagicMock()
    mock_tab = MagicMock()
    mock_tab.url = "about:blank"
    mock_browser.get_latest_tab.return_value = mock_tab
    return mock_browser

def test_v2_hitl():
    print("🧪 [Test] Starting Human-in-the-Loop (HITL) Test...")
    
    memory = MemorySaver()
    graph = build_graph(checkpointer=memory)
    
    config = {
        "configurable": {
            "thread_id": "test_thread_hitl",
            "browser": mock_browser_driver()
        }
    }
    
    # 1. Start Task
    print("\n▶️ Step 1: 启动任务 (Planner -> Coder)")
    initial_state = {
        "user_task": "Go to Google",
        "loop_count": 0,
        "finished_steps": [],
        "messages": []
    }
    
    # 我们期望 Graph 运行到 'Executor' 之前停止
    # 所以它应该执行 Planner -> Coder -> PAUSE
    event_count = 0
    for event in graph.stream(initial_state, config=config, stream_mode="updates"):
        for node_name, updates in event.items():
            print(f"   🔄 Node executed: {node_name}")
            event_count += 1
            
    snapshot = graph.get_state(config)
    print(f"   ⏸️ Stopped at: {snapshot.next}")
    
    if "Executor" in snapshot.next:
        print("   ✅ HITL Verified: Graph paused before Executor.")
    else:
        print(f"   ❌ HITL Failed: Graph did not pause at Executor. Next: {snapshot.next}")
        return

    # 2. Human Approval (Resume)
    print("\n▶️ Step 2: 人工批准 (Resuming...)")
    # LangGraph 中，传入 None 或新的 Command 即可继续
    # 这里我们模拟用户批准代码通过，不修改 state
    
    # 注意：stream(None, config) 会继续由于 interrupt 而暂停的线程
    try:
        for event in graph.stream(None, config=config, stream_mode="updates"):
             for node_name, updates in event.items():
                print(f"   🔄 Node executed (after resume): {node_name}")
        
        print("   ✅ Resume Verified: Graph continued execution.")
        
    except Exception as e:
        print(f"   ⚠️ Resume Error: {e}")

if __name__ == "__main__":
    test_v2_hitl()
