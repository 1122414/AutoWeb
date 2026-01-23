import os
import json
import csv
import sqlite3
import httpx
import re
from typing import List, Dict, Union, Optional
from skills.tool_rag import save_to_knowledge_base # RAG Ingestion

# ==============================================================================
# AutoWeb Standard Library (ASL)
# ==============================================================================

# 1. ⚡ Direct HTTP
def http_request(url: str, method: str="GET", headers: Dict = None, params: Dict = None, data: Dict = None) -> str:
    """
    [Network] 直接发送 HTTP 请求，绕过浏览器渲染。适合抓取 API 或纯静态页面。
    """
    if headers is None:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
    
    print(f"⚡ [Toolbox] HTTP {method} -> {url}")
    try:
        with httpx.Client(timeout=30.0, verify=False) as client:
            resp = client.request(method, url, headers=headers, params=params, json=data)
            resp.raise_for_status()
            return resp.text
    except Exception as e:
        return f"Error: {str(e)}"

# 2. 📥 File Downloader
def download_file(url: str, save_path: str) -> bool:
    """
    [Network] 下载文件到本地。
    """
    print(f"📥 [Toolbox] Downloading: {url} -> {save_path}")
    try:
        with httpx.stream("GET", url, verify=False, timeout=60.0) as resp:
            resp.raise_for_status()
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
        return True
    except Exception as e:
        print(f"❌ [Toolbox] Download Failed: {e}")
        return False

# 3. 🧹 Content Cleaner
def clean_html(html: str) -> str:
    """
    [Parser] 简单的 HTML 清洗，去除 script/style/注释，返回纯文本结构
    """
    if not html: return ""
    # 去除 script/style
    text = re.sub(r'<(script|style).*?>.*?</\1>', '', html, flags=re.DOTALL)
    # 去除注释
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    # 去除 HTML 标签 (简单版)
    text = re.sub(r'<.*?>', ' ', text)
    # 去除多余空格
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# 4. 🍪 Cookie Manager
def load_cookies_from_str(cookie_str: str, domain: str) -> List[Dict]:
    """
    [Browser] 解析 EditThisCookie 格式或 Header 格式的 Cookie 字符串
    """
    cookies = []
    # Case A: JSON List (EditThisCookie)
    if cookie_str.strip().startswith("["):
        try:
            raw_list = json.loads(cookie_str)
            for item in raw_list:
                cookies.append({
                    "name": item.get("name"),
                    "value": item.get("value"),
                    "domain": item.get("domain", domain),
                    "path": item.get("path", "/")
                })
            return cookies
        except: pass
    
    # Case B: Header String (k=v; k=v)
    parts = cookie_str.split(";")
    for part in parts:
        if "=" in part:
            k, v = part.strip().split("=", 1)
            cookies.append({
                "name": k, 
                "value": v, 
                "domain": domain, 
                "path": "/"
            })
    return cookies

# 5. 💾 Database Persistence (SQLite)
def db_insert(table: str, data: Dict, db_path: str = "autoweb_data.db"):
    """
    [DB] 将字典数据插入 SQLite 数据库。会自动建表。
    """
    print(f"💾 [Toolbox] DB Insert -> Table: {table}")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. 自动建表 (Simplistic: 假设所有字段都是 TEXT)
        keys = list(data.keys())
        if not keys: return
        
        cols_def = ", ".join([f"{k} TEXT" for k in keys])
        create_sql = f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY AUTOINCREMENT, {cols_def}, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        cursor.execute(create_sql)
        
        # 2. 检查是否有新列 (Schema Evolution - 略过, 假设 Schema 稳定)
        
        # 3. 插入数据
        cols = ", ".join(keys)
        placeholders = ", ".join(["?" for _ in keys])
        values = [str(data[k]) for k in keys]
        
        sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
        cursor.execute(sql, values)
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"❌ [Toolbox] DB Error: {e}")
        return False

def db_query(sql: str, db_path: str = "autoweb_data.db") -> List[Dict]:
    """
    [DB] 执行 SQL 查询
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row # 返回字典接口
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        result = [dict(row) for row in rows]
        conn.close()
        return result
    except Exception as e:
        print(f"❌ [Toolbox] Query Error: {e}")
        return []

# 6. 📊 Excel/CSV Export
def save_to_csv(data_list: List[Dict], filename: str):
    """
    [Data] 保存数据列表到 CSV
    """
    if not data_list: return
    print(f"📊 [Toolbox] Saving CSV -> {filename}")
    try:
        keys = data_list[0].keys()
        # Handle unicode in Windows
        mode = 'a' if os.path.exists(filename) else 'w'
        with open(filename, mode, newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            if mode == 'w':
                writer.writeheader()
            writer.writerows(data_list)
        return True
    except Exception as e:
        print(f"❌ [Toolbox] CSV Error: {e}")
        return False

# 7. 📧 Notification (Mock)
def notify(msg: str, title: str = "AutoWeb Notification"):
    """
    [Notify] 发送通知 (目前只打印，未来可对接 Email/Slack)
    """
    print(f"\n🔔 [{title}] {msg}\n")
    return True
