"""
验证修复效果：确认动态字段作为独立字段写入 Milvus
用法：先删旧数据，运行此脚本写入测试数据，再查询验证
"""
from pymilvus import connections, Collection, utility
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# 1. 连接
connections.connect(alias="default", host="100.90.245.123", port="19530")
col = Collection("spider_knowledge_base")

# 2. 先看当前数据
print(f"当前实体数: {col.num_entities}")
res = col.query(expr="pk >= 0", output_fields=[
                "coding_index", "model_name", "text"], limit=3)
print(f"\n=== 当前数据 (前3条) ===")
for r in res:
    ci = r.get("coding_index")
    mn = r.get("model_name")
    pk = r.get("pk")
    text_preview = str(r.get("text", ""))[:80]
    print(f"  pk={pk}")
    print(f"    coding_index: {repr(ci)} (type={type(ci).__name__})")
    print(f"    model_name:   {repr(mn)} (type={type(mn).__name__})")
    print(f"    text preview: {text_preview}...")
    print()

# 3. 测试 expr 过滤
print("=== 测试 expr 过滤 ===")
for expr in ["coding_index >= 0", 'model_name != ""']:
    try:
        count = col.query(expr=expr, output_fields=["pk"], limit=1000)
        print(f"  expr='{expr}' → {len(count)} 条")
    except Exception as e:
        print(f"  expr='{expr}' → 错误: {e}")
