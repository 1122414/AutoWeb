import os
import sys
import time

import pytest


if os.getenv("AUTOWEB_RUN_INTEGRATION") != "1":
    pytest.skip(
        "requires live Milvus and embedding dependencies",
        allow_module_level=True,
    )

from skills.code_cache import code_cache_manager, CacheHit

# 将项目根目录添加到 sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# 模拟配置
os.environ["CODE_CACHE_WEIGHT_GOAL"] = "0.6"
os.environ["CODE_CACHE_WEIGHT_LOCATOR"] = "0.3"
os.environ["CODE_CACHE_WEIGHT_URL"] = "0.1"


def test_code_cache_workflow():
    print("\n🚀 开始测试 Code Cache V6 (多向量融合方案)...")

    # 1. 模拟数据插入
    print("\n[Step 1] 存储测试数据...")

    # Case A: 搜索 sea (原始任务)
    code_cache_manager.save(
        goal="在搜索框输入 sea 并点击搜索",
        dom_skeleton="<html>...search_box...</html>",
        url="https://www.mard.gov.vn/en/Pages/default.aspx",
        code="tab.ele('#txtInput').input('sea'); tab.ele('#btnSearch').click()",
        user_task="去这个网站搜索sea，爬取结果",
        locator_info="#txtInput | #btnSearch"
    )

    # Case B: 爬取结果 (后续任务)
    code_cache_manager.save(
        goal="爬取搜索结果表格",
        dom_skeleton="<html>...result_table...</html>",
        url="https://www.mard.gov.vn/en/Pages/search.aspx",
        code="data = []; for row in tab.eles('css:.result-row'): ...",
        user_task="去这个网站搜索sea，爬取结果",
        locator_info=".result-row | .title | .date"
    )

    # 等待异步写入完成
    time.sleep(2)

    # 2. 验证精确召回 (Case A)
    print("\n[Step 2] 验证精确召回 (Target: Case A)...")
    hits = code_cache_manager.search(
        user_task="去这个网站搜索sea，爬取结果",
        goal="在搜索框输入 sea 并点击搜索",  # 完全匹配 Case A 的 goal
        url="https://www.mard.gov.vn/en/Pages/default.aspx",
        locator_info="#txtInput | #btnSearch"
    )

    if hits:
        top_hit = hits[0]
        print(
            f"   Top Hit: {top_hit.goal[:30]}... (Score: {top_hit.score:.4f})")
        if "sea" in top_hit.code:
            print("   ✅ 成功召回 Case A 代码")
        else:
            print("   ❌ 召回了错误的代码")
    else:
        print("   ❌ 未召回任何结果")

    # 3. 验证区分度 (Case B)
    print("\n[Step 3] 验证区分度 (Target: Case B)...")
    # 注意：user_task 和 Case A 一样，但 goal 和 locator 不同
    hits = code_cache_manager.search(
        user_task="去这个网站搜索sea，爬取结果",
        goal="提取表格数据",  # 语义接近 Case B
        url="https://www.mard.gov.vn/en/Pages/search.aspx",
        locator_info=".result-row"
    )

    if hits:
        top_hit = hits[0]
        print(
            f"   Top Hit: {top_hit.goal[:30]}... (Score: {top_hit.score:.4f})")
        if "data = []" in top_hit.code:
            print("   ✅ 成功召回 Case B 代码 (尽管 user_task 相同)")
        else:
            print(f"   ❌ 召回了错误的代码: {top_hit.code[:50]}...")
    else:
        print("   ❌ 未召回任何结果")


if __name__ == "__main__":
    # 确保在项目根目录运行
    sys.path.append(os.getcwd())
    test_code_cache_workflow()
