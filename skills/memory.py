import os
import csv
import json
import pandas as pd
import psycopg2
from datetime import datetime
from typing import List, Dict, Any, Union
from psycopg2.extras import Json

# 向量数据库相关
from langchain_openai import OpenAIEmbeddings
from langchain_milvus import Milvus
from config import (
    POSTGRES_CONNECTION_STRING, 
    MILVUS_URI, 
    COLLECTION_NAME, 
    OUTPUT_DIR
)

class AgentMemory:
    """
    [记忆存储单元]
    负责：结构化数据存储(CSV/DB)、非结构化知识入库(Vector DB)
    """
    
    def __init__(self):
        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)

    def _get_timestamp(self):
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    # ================= 文件存储 (Short-term) =================

    def save_to_local_file(self, data: List[Dict], prefix: str = "automation_result", file_type: str = "json") -> str:
        """保存数据到本地文件 (JSON/CSV)"""
        if not data:
            return "No data to save."
            
        filename = f"{prefix}_{self._get_timestamp()}.{file_type}"
        filepath = os.path.join(OUTPUT_DIR, filename)

        try:
            if file_type == "json":
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            elif file_type == "csv":
                df = pd.DataFrame(data)
                df.to_csv(filepath, index=False, encoding='utf-8-sig')
            
            print(f"💾 [Memory] Saved local file: {filepath}")
            return filepath
        except Exception as e:
            return f"Save failed: {str(e)}"

    # ================= 数据库存储 (Long-term) =================

    def save_to_postgres(self, data: List[Dict], table_name: str = "captured_data") -> str:
        """保存到 PostgreSQL"""
        if not data or not POSTGRES_CONNECTION_STRING:
            return "Skipped DB save (No data or No DSN)."

        try:
            conn = psycopg2.connect(POSTGRES_CONNECTION_STRING)
            cur = conn.cursor()
            # 自动建表 (简化版，生产环境建议用 Migration 工具)
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id SERIAL PRIMARY KEY,
                    captured_at TIMESTAMP DEFAULT NOW(),
                    payload JSONB
                );
            """)
            
            # 批量插入
            args_list = [(Json(item),) for item in data]
            cur.executemany(f"INSERT INTO {table_name} (payload) VALUES (%s)", args_list)
            
            conn.commit()
            cur.close()
            conn.close()
            print(f"💾 [Memory] Saved {len(data)} records to DB table '{table_name}'")
            return "Success"
        except Exception as e:
            print(f"❌ DB Error: {e}")
            return str(e)

    # ================= 向量知识库 (RAG Knowledge) =================

    def ingest_to_vector_db(self, text_chunks: List[str], metadatas: List[Dict]) -> str:
        """
        将文本片段向量化并存入 Milvus
        """
        if not text_chunks: return "No text to ingest."
        
        try:
            print(f"🧠 [Memory] Embedding {len(text_chunks)} chunks...")
            embeddings = OpenAIEmbeddings() # 默认使用环境变量中的 OPENAI_API_KEY
            
            vector_store = Milvus(
                embedding_function=embeddings,
                connection_args={"uri": MILVUS_URI},
                collection_name=COLLECTION_NAME,
                auto_id=True
            )
            
            vector_store.add_texts(
                texts=text_chunks,
                metadatas=metadatas
            )
            return f"Successfully ingested {len(text_chunks)} chunks into Milvus."
        except Exception as e:
            print(f"❌ Vector DB Error: {e}")
            return str(e)