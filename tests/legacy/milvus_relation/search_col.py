from pymilvus import connections, utility, Collection
connections.connect(alias="default", host="100.90.245.123", port="19530")
col = Collection("dom_cache")
print(col.num_entities)  # 现在应为 0
res = col.query(
    expr="pk >= 0",
    output_fields=["text", "source", "coding_index"],
    limit=1000
)
for r in res:
    print(r)
