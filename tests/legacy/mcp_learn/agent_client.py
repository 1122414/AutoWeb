import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
# 引入 MCP 客户端和 LangChain 适配器
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools
load_dotenv()
async def run_agent():
    # 1. 配置如何连接到我们刚才写的 MCP Server
    # 这里使用 stdio 模式，相当于作为子进程启动 server.py
    server_file = str(Path(__file__).with_name("server.py"))
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[server_file],
    )

    # 2. 建立与 MCP Server 的连接
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # 初始化会话
            await session.initialize()
            
            # 3. 【核心魔法】动态加载远端 MCP Server 上的所有 Tools！
            # 这一步会自动把 get_system_memory 和 send_app_notification 转成 LangChain Tool
            mcp_tools = await load_mcp_tools(session)
            print(f"成功从 MCP Server 加载了 {len(mcp_tools)} 个工具: {[t.name for t in mcp_tools]}")

            # 4. 初始化你的大模型，并绑定这些动态加载的工具
            llm = ChatOpenAI(model=os.getenv("BAILIAN_MODEL_NAME"), base_url=os.getenv("BAILIAN_BASE_URL"), api_key=os.getenv("BAILIAN_API_KEY"))
            llm_with_tools = llm.bind_tools(mcp_tools)

            # 5. 测试 Agent 意图识别与工具调用
            query = "用户反馈手机很卡，帮我查一下当前内存占用。如果占用超过 80%，发个弹窗提醒用户清理空间。"
            print(f"User Query: {query}\n")
            
            messages = [HumanMessage(content=query)]
            
            # 第一轮 LLM 推理 (它会决定调用 get_system_memory)
            ai_msg = await llm_with_tools.ainvoke(messages)
            messages.append(ai_msg)
            
            # 如果大模型决定调用工具，执行并返回结果
            if ai_msg.tool_calls:
                for tool_call in ai_msg.tool_calls:
                    print(f"🤖 Agent 决定调用工具: {tool_call['name']}，参数: {tool_call['args']}")
                    # 在 mcp_tools 列表中找到对应的工具并执行
                    selected_tool = next(t for t in mcp_tools if t.name == tool_call['name'])
                    tool_result = await selected_tool.ainvoke(tool_call['args'])
                    print(f"🛠️ 工具执行结果: {tool_result}")
                    
                    # 你可以在这里继续把结果放入 messages 传给 LLM 进行下一轮推理...

if __name__ == "__main__":
    asyncio.run(run_agent())