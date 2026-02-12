# AutoWeb 工具箱与代码缓存升级总结 (V5)

本项目近期完成了两项核心底层升级：**数据存储的域名隔离** 以及 **高效率的代码缓存参数复用**。

## 1. 数据存储域名隔离

为了解决 `output/` 目录下文件堆积且难以区分的问题，现在实现了自动化的域名分级存储。

### 核心变更

- **自动化路径**：`toolbox.save_data` 会根据当前浏览器 Tab 的 URL 自动提取域名。
- **目录结构**：数据将按格式存入 `output/{domain}/{filename}_{timestamp}.{ext}`。
- **环境注入**：在 `nodes.py` 执行 Python 策略前，会自动调用 `set_current_url` 确保工具箱感知当前上下文。

### 带来的价值

- 爬取海量网站数据时，文件结构井然有序。
- 后续 RAG 节点读取数据时可通过递归 glob 轻松定位特定站点的数据。

---

## 2. 代码缓存参数感知复用 (Zero-Token Approach)

为了解决相似任务（仅搜索参数不同，如 "fish" vs "fishery"）导致的模型重复生成代码或复用错误代码的问题。

### 核心变更

- **动态参数提取**：新增 `extract_param_diffs` 函数，通过对比“缓存任务”和“当前任务”的差异，定位变化的字符串。
- **程序化替换**：新增 `apply_param_substitution` 函数。在命中缓存后，自动在代码的**字符串字面量**中替换这些差异参数。
- **元数据增强**：Milvus 存储中新增了 `user_task` 原始字段（通过启用 `enable_dynamic_field` 实现）。

### 带来的价值

- **零 Token 消耗**：参数微调任务不再需要调用 LLM 重写代码，直接程序化复用。
- **匹配更精准**：由 `user_task + goal + url + locators` 组成的混合向量替代了无序的 DOM 向量。

---

## 3. Embedding 权重优化

解决了同一页面不同任务相似度虚高（全是 0.94+）的问题。

### 核心变更

- **移除 DOM 干扰**：从 Embedding 文本中移除了长达 2500 字符的 raw DOM（它占据了 90% 以上的权重，导致任务差异被掩盖）。
- **加入定位属性**：改用 Observer 分析出的唯一 `locator (reason)` 作为结构特征。
- **信号放大**：将 `Task` 和 `Goal` 在 Embedding 文本中重复出现，以提升检索权重。

---

## 4. 开发说明

- **代码位置**：主要逻辑位于 `skills/toolbox.py`, `skills/code_cache.py` 和 `core/nodes.py`。
- **维护建议**：如果修改了 `locator_suggestions` 的数据格式，请同步更新 `nodes.py` 中的 `_extract_locator_info` 辅助函数。
