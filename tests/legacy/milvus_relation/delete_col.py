from pymilvus import connections, utility, Collection
connections.connect(alias="default", host="100.90.245.123", port="19530")
# col = Collection("spider_knowledge_base")
# col.compact()
# col.wait_for_compaction_completed()
# col.flush()
# print(col.num_entities)  # 现在应为 0
# res = col.query(
#     expr="pk >= 0",
#     output_fields=["text", "source"],
#     limit=1000
# )
# print(res)

collection_name = "dom_cache"

# 2. 检查集合是否存在
if utility.has_collection(collection_name):
    # 3. 实例化对象并删除
    col = Collection(collection_name)
    col.drop()
    print(f"✅ 集合 '{collection_name}' 已成功删除。")
else:
    print(f"⚠️ 集合 '{collection_name}' 不存在，无需操作。")

# collection = Collection("spider_knowledge_base")

# # # 使用表达式删除所有数据
# # # 假设你的主键名是 "pk"
# res = collection.delete(expr="pk >= 0")

# print(f"✅ 已发起删除请求。影响行数: {res.delete_count}")


# 删除所有集合
# connections.connect(host="localhost", port="19530")

# # 获取当前数据库中所有的集合名称
# all_collections = utility.list_collections()

# print(f"发现 {len(all_collections)} 个集合: {all_collections}")

# for name in all_collections:
#     utility.drop_collection(name)
#     print(f"🗑️ 已删除: {name}")

# print("✨ 所有集合已清理完毕。")
