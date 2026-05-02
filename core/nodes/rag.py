from __future__ import annotations

import json
from typing import Literal

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from core.state_v2 import AgentState
from core.nodes._cache import _save_code_to_cache, _save_dom_to_cache
from skills.logger import logger

def rag_node(state: AgentState, config: RunnableConfig) -> Command[Literal["Observer"]]:
    """
    [RAG Node] 统一处理所有向量数据库操作

    设计说明:
        rag_task_type 由上游节点（Planner / Verifier）写入 State，
        RAGNode 读取后分派。这是 LangGraph Command 模式下的惯用法——
        Command(goto=...) 只能指定目标节点，无法传递额外参数，
        因此必须通过 State 携带路由上下文。

    任务类型:
    - store_kb: 读取最新 JSON → 存入知识库
    - store_cache: 将验证通过的代码存入 Code Cache 和 Dom Cache
    - qa: 查询知识库并返回答案
    """
    rag_task = state.get("rag_task_type")
    logger.info(f"\n📚 [RAG Node] 任务类型: {rag_task}")

    result_summary = ""

    try:
        if rag_task == "store_kb":
            result_summary = _rag_store_kb(state)

        elif rag_task == "store_cache":
            result_summary = _rag_store_cache(state, config)

        elif rag_task == "qa":
            result_summary = _rag_qa(state)

        else:
            result_summary = f"未知的 RAG 任务类型: {rag_task}"
            logger.warning(f"   ⚠️ {result_summary}")

    except Exception as e:
        result_summary = f"RAG 执行失败: {e}"
        logger.error(f"   ❌ {result_summary}")

    logger.info(f"   📋 RAG 结果: {result_summary[:100]}")

    return Command(
        update={
            "messages": [AIMessage(content=f"[RAG] {result_summary}")],
            "rag_task_type": None,  # 清空任务标记
            "finished_steps": [result_summary] if rag_task != "store_cache" else [],
        },
        goto="Observer"
    )


def _rag_store_kb(state: AgentState) -> str:
    """[RAG] 将最新输出数据存入知识库（支持 JSON / CSV / SQLite）"""
    import glob
    import os
    import csv
    import sqlite3

    # 1. 查找 output 目录下最新的数据文件（支持域名子目录）
    files = glob.glob("output/**/*.json", recursive=True) + \
        glob.glob("output/**/*.csv", recursive=True) + \
        glob.glob("output/**/*.jsonl", recursive=True)

    # 同时检查 SQLite 数据库
    db_files = glob.glob("*.db") + glob.glob("output/*.db")

    all_sources = files + db_files
    if not all_sources:
        return "未找到任何数据文件（output/*.json, *.csv, *.db）"

    latest_file = max(all_sources, key=os.path.getmtime)
    ext = os.path.splitext(latest_file)[1].lower()
    logger.info(f"   📂 最新数据文件: {latest_file} (格式: {ext})")

    data = []

    # 2. 根据格式读取数据
    if ext == ".json":
        with open(latest_file, encoding="utf-8") as f:
            raw = json.load(f)
            data = raw if isinstance(raw, list) else [raw]

    elif ext == ".jsonl":
        with open(latest_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data.append(json.loads(line))

    elif ext == ".csv":
        with open(latest_file, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            data = [dict(row) for row in reader]

    elif ext == ".db":
        conn = sqlite3.connect(latest_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        # 获取所有用户表
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = [row[0] for row in cursor.fetchall()]
        for table in tables:
            cursor.execute(f"SELECT * FROM {table}")
            rows = cursor.fetchall()
            for row in rows:
                data.append(dict(row))
        conn.close()
        logger.info(f"   📊 从 SQLite 读取 {len(tables)} 张表")

    if not data:
        return f"文件 {latest_file} 中无有效数据"

    logger.info(f"   📊 数据条数: {len(data)}")

    # 3. 存入知识库
    from skills.toolbox import save_to_kb, flush_kb

    source = state.get("current_url", "auto_crawl")
    save_to_kb(data, source=source)
    flush_kb()

    return f"成功将 {len(data)} 条数据从 {latest_file} 存入向量知识库 (save_to_kb)"


def _rag_store_cache(state: AgentState, config: RunnableConfig) -> str:
    """[RAG] 将验证通过的代码/策略存入 Code Cache / Dom Cache"""
    current_url = state.get("current_url", "")

    res_code = "跳过"
    res_dom = "跳过"

    # 存 Code Cache
    if state.get("generated_code") and len(state.get("generated_code", "")) >= 50 and state.get("_code_source") != "cache":
        result_code = _save_code_to_cache(state, current_url)
        res_code = result_code.get("false", result_code.get("true", "未知"))

    # 存 Dom Cache
    if state.get("_observer_source") == "observer":
        result_dom = _save_dom_to_cache(state, current_url)
        res_dom = result_dom.get("false", result_dom.get("true", "未知"))

    return f"代码缓存: {res_code}, DOM缓存: {res_dom}"


def _rag_qa(state: AgentState) -> str:
    """[RAG] 查询知识库并返回答案"""
    from skills.tool_rag import ask_knowledge_base

    # 从 plan 中提取问题
    plan = state.get("plan", "")
    # 清理计划格式，提取实际问题
    question = plan.replace("【计划已生成】", "").strip()
    # 去掉行号前缀
    lines = question.split("\n")
    if lines:
        question = lines[0].strip()
        if question.startswith("1."):
            question = question[2:].strip()

    logger.info(f"   🔍 查询: {question}")
    answer = ask_knowledge_base(question)
    return f"知识库问答完成: {answer[:200]}"