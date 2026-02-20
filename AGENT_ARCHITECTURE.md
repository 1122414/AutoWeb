# AutoWeb Agent 架构与工具调用模式详解 (最终修订版)

本文档旨在说明 AutoWeb Agent 的核心运行架构以及它如何与底层工具（Toolbox）进行交互。

## 1. 核心架构：LangGraph 状态机

你的 Agent **不是**一个简单的线性脚本，而是基于 `LangGraph` 构建的**有向图 (Directed Graph)**。

- **状态流转**：通过 `AgentState` 在不同节点（Nodes）之间传递上下文（如 URL、任务、生成的代码、DOM 骨架等）。
- **逻辑控制**：由 `Planner` 节点负责决策逻辑分支，决定跳转到 `Coder`、`RAGNode` 还是 `__end__`。

## 2. 工具调用模式：代码即工具 (Code-as-a-Tool)

Agent 调用工具（如保存数据、操作浏览器）的方式并非传统的 LLM Function Calling，而是**生成并执行 Python 代码**。

### 工作流：

1. **定义 (Definition)**：所有原子工具函数位于 `skills/toolbox.py`。
2. **生成 (Generation)**：`Coder` 节点基于 `coder_prompts.py` 的指令，生成调用 `toolbox` 的 Python 代码。
3. **注入 (Injection)**：在 `actor.py` 的执行器中，会将 `toolbox`, `tab`, `browser` 等对象注入到 Python 的执行环境。
4. **执行 (Execution)**：通过 `exec()` 动态运行脚本。这种方式避免了传统 Tool Calling 在复杂逻辑（如循环爬取）下的死板。

## 3. RAG 节点的特殊属性：静态工具路由 (Static Tool Routing)

用户经常会问：**RAGNode 算不算 Agent 的工具调用？**

**答案：**

- **广义上算**：Agent 为了达成目标使用了外部能力（存取向量库），这就是使用了工具。
- **技术实现上不算**：它**没有**使用 LLM 的 `bind_tools` 或 `tool_calls` API。

**它的工作原理是：**

1.  **意图识别**：`Planner` (LLM) 在 `task` 描述中输出自然语言指令（如“存入知识库”）。
2.  **关键词匹配**：Python 代码解析这段描述，识别关键词，给 State 打上 `rag_task_type` 标签。
3.  **静态路由**：LangGraph 根据标签无条件跳转到 `RAGNode`。
4.  **函数直调**：`RAGNode` 直接调用 Python 函数 `_rag_store_kb`。

**为什么这样做？**

- **确定性 (Deterministic)**：数据库操作是敏感的，我们希望它 100% 受控，而不是依赖模型概率性生成的 Tool Call。
- **解耦 (Decoupling)**：将复杂的 RAG 逻辑从 LLM 的 Context Window 中移出，让模型专注于规划，让代码负责执行。

## 4. 与其他模式的对比

| 维度         | AutoWeb 模式                         | 传统 Tool Calling       | MCP (Model Context Protocol) |
| :----------- | :----------------------------------- | :---------------------- | :--------------------------- |
| **调用载体** | 动态生成的 Python 代码               | 预定义的 JSON 参数      | 标准化协议接口               |
| **灵活性**   | **极高**（可写逻辑、循环、复杂处理） | 中（受限于参数结构）    | 高（生态化接入）             |
| **透明度**   | 高（生成的代码可见、可记录）         | 低（黑盒调用）          | 高（协议透明）               |
| **实现框架** | LangGraph + custom Actor             | Langchain AgentExecutor | MCP Host/Server              |

## 总结

AutoWeb 采用的是一种**“LLM 编写脚本 -> 环境沙箱执行”**的高端架构。它赋予了模型完全自由的 Python 能力来处理网页上的复杂数据，同时通过 `toolbox.py` 提供了一套经过封装的、更安全的标准库供模型调用。
