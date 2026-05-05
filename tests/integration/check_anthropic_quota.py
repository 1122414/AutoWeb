"""
Anthropic API 使用情况查询脚本
由 Claude Sonnet 4.5 生成

注意：Anthropic 没有提供直接查询 API 额度的公开端点。
本脚本提供 API 密钥验证和使用指引。
"""

import os
import sys
from typing import Optional

try:
    import requests
    from anthropic import Anthropic
except ImportError as e:
    print(f"缺少依赖库: {e}")
    print("请运行: pip install anthropic requests")
    sys.exit(1)


def check_api_key(api_key: Optional[str] = None) -> bool:
    """
    验证 API 密钥是否有效

    Args:
        api_key: Anthropic API 密钥，如果不提供则从环境变量读取

    Returns:
        bool: API 密钥是否有效
    """
    if api_key is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")

    if not api_key:
        print("❌ 未找到 API 密钥")
        print("请设置环境变量 ANTHROPIC_API_KEY 或直接传入密钥")
        return False

    print(f"API 密钥 (前8位): {api_key[:8]}...{api_key[-4:]}")

    try:
        client = Anthropic(api_key=api_key)

        # 尝试调用一个简单的 API 来验证密钥
        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=10,
            messages=[{"role": "user", "content": "Hi"}]
        )

        print("✅ API 密钥有效")
        print(f"模型: {response.model}")
        print(f"使用情况: {response.usage}")
        return True

    except Exception as e:
        print(f"❌ API 密钥验证失败: {e}")
        return False


def get_quota_from_console() -> str:
    """
    获取查看额度的控制台链接

    Returns:
        str: 控制台 URL
    """
    return "https://console.anthropic.com/settings/plans"


def check_usage_via_api(api_key: Optional[str] = None) -> dict:
    """
    尝试通过 API 获取使用情况

    注意：这需要账户管理员权限

    Args:
        api_key: Anthropic API 密钥

    Returns:
        dict: 使用情况信息
    """
    if api_key is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }

    # 尝试获取账户信息（可能不可用）
    urls_to_try = [
        "https://api.anthropic.com/v1/account",
        "https://api.anthropic.com/v1/billing",
        "https://api.anthropic.com/v1/usage"
    ]

    results = {}
    for url in urls_to_try:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                results[url] = response.json()
            else:
                results[url] = f"状态码: {response.status_code}"
        except Exception as e:
            results[url] = f"错误: {str(e)}"

    return results


def main():
    """主函数"""
    print("=" * 60)
    print("Anthropic API 使用情况查询")
    print("=" * 60)
    print()

    # 检查 API 密钥
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("请设置 ANTHROPIC_API_KEY 环境变量")
        print()
        print("示例:")
        print("  export ANTHROPIC_API_KEY='your-key-here'  # Linux/Mac")
        print("  set ANTHROPIC_API_KEY=your-key-here       # Windows")
        print()
        api_key = input("或者直接输入你的 API 密钥: ").strip()

    print()

    # 验证 API 密钥
    if check_api_key(api_key):
        print()
        print("-" * 60)
        print("💡 如何查看 API 额度:")
        print("-" * 60)
        print()
        print("1. 访问 Anthropic Console:")
        print(f"   {get_quota_from_console()}")
        print()
        print("2. 在控制台中查看:")
        print("   - Usage (使用情况)")
        print("   - Billing (账单)")
        print("   - Plans & Limits (计划和限制)")
        print()
        print("3. 常见端点:")
        print("   - 当前账单周期使用量")
        print("   - 历史使用记录")
        print("   - 余额和充值")
        print()

        # 尝试通过 API 获取信息
        print("-" * 60)
        print("🔍 尝试通过 API 获取账户信息...")
        print("-" * 60)
        usage_results = check_usage_via_api(api_key)
        for url, result in usage_results.items():
            print(f"\n{url}")
            print(f"  结果: {result}")

        print()
        print("⚠️  注意: 大多数账户级别的信息需要通过控制台查看")
        print("    API 端点主要用于模型推理，不提供额度查询")

    print()
    print("=" * 60)


if __name__ == "__main__":
    main()
