from mcp.server.fastmcp import FastMCP
import psutil
import sys

# 1. 初始化一个 MCP Server
mcp = FastMCP("QQ_Client_Native_Skills")

# 2. 使用 @mcp.tool 装饰器暴露一个函数作为大模型的 Tool
@mcp.tool()
def get_system_memory() -> str:
    """
    获取当前系统或设备的内存使用率。
    当 Agent 需要诊断设备卡顿或性能瓶颈时调用。
    """
    mem = psutil.virtual_memory()
    return f"当前设备内存使用率: {mem.percent}%，剩余可用内存: {mem.available / (1024 ** 3):.2f} GB"

@mcp.tool()
def send_app_notification(title: str, message: str) -> str:
    """
    向移动端 APP 或桌面系统发送一条本地系统通知弹窗。
    """
    # 这里可以是调用底层 C++/JS 接口的代码，这里用打印模拟
    print(f"🔔 [系统通知触发] {title}: {message}", file=sys.stderr, flush=True)
    return "通知发送成功"

# 3. 提供 Resource（只读上下文，非执行动作，比如读取本地配置文件）
@mcp.resource("config://app/settings")
def get_app_settings() -> str:
    """读取客户端的配置状态"""
    return '{"theme": "dark", "auto_update": true, "version": "9.1.0"}'

if __name__ == "__main__":
    # 启动 MCP Server (默认通过 stdio 标准输入输出与 Client 通信，最适合本地 Agent 进程间通信)
    print("启动 QQ_Client_Native_Skills MCP Server...", file=sys.stderr, flush=True)
    mcp.run()