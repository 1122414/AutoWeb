from transformers import AutoModel, AutoTokenizer

# 1. 加载你微调好的 Qwen 模型
model = AutoModel.from_pretrained("path/to/your_finetuned_qwen").cuda()
tokenizer = AutoTokenizer.from_pretrained("path/to/your_finetuned_qwen")

# 2. 输入一条黑话消息
text = "诚信出U，汇率可谈，量大优先"
inputs = tokenizer(text, return_tensors="pt").to("cuda")

# 3. 获取模型的输出
outputs = model(**inputs)

# 4. 提取最后一层的隐藏状态，取平均值或首token作为句子的表征向量
# 这个 text_embedding 就是这条消息的“数字替身”
text_embedding = outputs.last_hidden_state.mean(dim=1)
print(text_embedding.shape) # 例如：(1, 4096)