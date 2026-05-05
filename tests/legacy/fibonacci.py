# 斐波那契数列计算函数
# 本文件由 Claude Sonnet 4.5 (claude-sonnet-4-5-20250929) 生成
# 注意：不是 GLM 模型

def fibonacci_recursive(n: int) -> int:
    """
    使用递归方法计算斐波那契数列

    Args:
        n: 要计算的斐波那契数列位置

    Returns:
        第 n 个斐波那契数

    Raises:
        ValueError: 如果 n 为负数
    """
    if n < 0:
        raise ValueError("n 必须是非负整数")
    if n <= 1:
        return n
    return fibonacci_recursive(n - 1) + fibonacci_recursive(n - 2)


def fibonacci_iterative(n: int) -> int:
    """
    使用迭代方法计算斐波那契数列（更高效）

    Args:
        n: 要计算的斐波那契数列位置

    Returns:
        第 n 个斐波那契数

    Raises:
        ValueError: 如果 n 为负数
    """
    if n < 0:
        raise ValueError("n 必须是非负整数")
    if n <= 1:
        return n

    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b


def fibonacci_sequence(n: int) -> list:
    """
    生成前 n 个斐波那契数

    Args:
        n: 要生成的斐波那契数的数量

    Returns:
        包含前 n 个斐波那契数的列表

    Raises:
        ValueError: 如果 n 为负数
    """
    if n < 0:
        raise ValueError("n 必须是非负整数")
    if n == 0:
        return []
    if n == 1:
        return [0]

    sequence = [0, 1]
    for i in range(2, n):
        sequence.append(sequence[i - 1] + sequence[i - 2])
    return sequence


if __name__ == "__main__":
    # 测试代码
    print("测试斐波那契数列函数\n")

    # 测试递归方法
    print("递归方法:")
    for i in range(10):
        print(f"F({i}) = {fibonacci_recursive(i)}")

    print("\n迭代方法:")
    for i in range(10):
        print(f"F({i}) = {fibonacci_iterative(i)}")

    print("\n前 15 个斐波那契数:")
    print(fibonacci_sequence(15))
