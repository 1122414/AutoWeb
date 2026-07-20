import os
import json
import csv
import sqlite3
import httpx
import re
from contextvars import ContextVar
from typing import List, Dict, Union
from urllib.parse import urlparse
from skills.logger import logger
from skills.tool_rag import kb_manager  # RAG Ingestion

# ==============================================================================
# 上下文变量：当前任务的 URL（由 Executor 在执行前设置，线程安全）
# ==============================================================================
_current_url: ContextVar[str] = ContextVar("_current_url", default="")


def set_current_url(url: str):
    """设置当前任务 URL（供 save_data 自动按域名分目录）"""
    _current_url.set(url or "")


def _get_domain_folder() -> str:
    """从 _current_url 提取域名作为子目录名"""
    url = _current_url.get()
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc
        # 去掉 www. 前缀和端口号
        domain = re.sub(r'^www\.', '', domain)
        domain = domain.split(':')[0]
        return domain if domain else ""
    except ValueError:
        return ""


# ==============================================================================
# AutoWeb Standard Library (ASL)
# ==============================================================================

# [NEW] 知识库存储接口


def save_to_kb(data: Union[List[Dict], Dict, str], source: str = "auto_crawl") -> bool:
    """
    [异步] 将爬取数据存入知识库（非阻塞）

    - 自动批量累积（达到 10 条自动写入）
    - 后台异步写入 Milvus 向量数据库
    - 程序退出时自动刷新缓冲

    Args:
        data: 文本、字典或字典列表
        source: 数据来源 URL 或标识

    Returns:
        bool: 是否成功加入缓冲

    Example:
        toolbox.save_to_kb({"title": "电影名", "year": 2024}, source="https://example.com")
        toolbox.save_to_kb([item1, item2, item3], source="crawl_task_1")
    """
    return kb_manager.add(data, source)


def flush_kb() -> bool:
    """
    强制刷新知识库缓冲（任务结束时调用）

    Returns:
        bool: 是否全部写入成功
    """
    return kb_manager.flush_and_wait()

# 1. ⚡ Direct HTTP


def http_request(url: str, method: str = "GET", headers: Dict = None, params: Dict = None, data: Dict = None) -> str:
    """
    [Network] 直接发送 HTTP 请求，绕过浏览器渲染。适合抓取 API 或纯静态页面。
    """
    if headers is None:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}

    logger.info(f"⚡ [Toolbox] HTTP {method} -> {url}")
    try:
        with httpx.Client(timeout=30.0, verify=False) as client:
            resp = client.request(
                method, url, headers=headers, params=params, json=data)
            resp.raise_for_status()
            return resp.text
    except httpx.HTTPError as e:
        logger.error(f"❌ [Toolbox] HTTP Error: {e}")
        return f"Error: {str(e)}"

# 2. 📥 File Downloader


def download_file(url: str, save_path: str) -> bool:
    """
    [Network] 下载文件到本地。
    """
    logger.info(f"📥 [Toolbox] Downloading: {url} -> {save_path}")
    try:
        with httpx.stream("GET", url, verify=False, timeout=60.0) as resp:
            resp.raise_for_status()
            parent = os.path.dirname(os.path.abspath(save_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(save_path, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
        return True
    except (httpx.HTTPError, IOError) as e:
        logger.error(f"❌ [Toolbox] Download Failed: {e}")
        return False

# 3. 🧹 Content Cleaner


def clean_html(html: str) -> str:
    """
    [Parser] 简单的 HTML 清洗，去除 script/style/注释，返回纯文本结构
    """
    if not html:
        return ""
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
        except json.JSONDecodeError:
            pass

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
    logger.info(f"💾 [Toolbox] DB Insert -> Table: {table}")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 1. 自动建表 (Simplistic: 假设所有字段都是 TEXT)
        keys = list(data.keys())
        if not keys:
            return

        cols_def = ", ".join([f"{k} TEXT" for k in keys])
        create_sql = f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY AUTOINCREMENT, {cols_def}, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        cursor.execute(create_sql)

        # 2. 插入数据
        cols = ", ".join(keys)
        placeholders = ", ".join(["?" for _ in keys])
        values = [str(data[k]) for k in keys]

        sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
        cursor.execute(sql, values)

        conn.commit()
        conn.close()
        return True
    except sqlite3.Error as e:
        logger.error(f"❌ [Toolbox] DB Error: {e}")
        return False


def db_query(sql: str, db_path: str = "autoweb_data.db") -> List[Dict]:
    """
    [DB] 执行 SQL 查询
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row  # 返回字典接口
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        result = [dict(row) for row in rows]
        conn.close()
        return result
    except sqlite3.Error as e:
        logger.error(f"❌ [Toolbox] Query Error: {e}")
        return []


def save_to_csv(data: Union[List[Dict], Dict], filename: str) -> bool:
    """Backward-compatible exact-path CSV adapter.

    Unlike ``save_data`` this helper intentionally does not add a timestamp;
    callers that supplied a concrete path keep ownership of that filename.
    """
    rows = data if isinstance(data, list) else [data]
    rows = [row for row in rows if isinstance(row, dict)]
    if not rows:
        return False
    try:
        parent = os.path.dirname(os.path.abspath(filename))
        if parent:
            os.makedirs(parent, exist_ok=True)
        fieldnames = list(
            dict.fromkeys(
                key
                for row in rows
                for key in row.keys()
            )
        )
        with open(
            filename,
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=fieldnames,
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)
        return True
    except (OSError, csv.Error, TypeError) as exc:
        logger.error(f"❌ [Toolbox] CSV Save Error: {exc}")
        return False


# 6. 💾 Unified Data Saver (The "Arm" for Coder)


def save_data(data: Union[List[Dict], Dict], filename: str, format: str = None):
    """
    [Data] 统一数据保存接口 (支持 json, jsonl, csv)
    - 自动根据文件扩展名推断格式（优先于 format 参数）
    - 自动添加时间戳防止覆盖
    - 自动创建父目录
    """
    import time as _time

    if not data:
        logger.warning("⚠️ [Toolbox] No data to save.")
        return False

    try:
        # 0. 根据扩展名推断格式（优先）
        basename = os.path.basename(filename)
        name_part, ext = os.path.splitext(basename)

        if ext:
            # 有扩展名，从扩展名推断格式
            inferred_format = ext[1:].lower()  # 去掉点号
            if inferred_format in ("json", "jsonl", "csv"):
                format = inferred_format

        # 如果还没有格式，使用默认值
        if not format:
            format = "json"

        # 1. 自动添加时间戳到文件名（防覆盖）
        timestamp = _time.strftime("%H%M%S")
        if ext:
            # 有扩展名：name.csv -> name_133000.csv
            new_filename = f"{name_part}_{timestamp}{ext}"
        else:
            # 无扩展名：自动补全
            new_filename = f"{name_part}_{timestamp}.{format}"

        # 保留目录路径，注入域名子目录
        dirname = os.path.dirname(filename)
        if not dirname:
            dirname = "output"  # 默认 output 目录

        # 自动按域名创建子目录
        domain_folder = _get_domain_folder()
        if domain_folder:
            dirname = os.path.join(dirname, domain_folder)

        filename = os.path.join(dirname, new_filename)

        logger.info(f"💾 [Toolbox] Saving {format.upper()} -> {filename}")

        # 2. 确保目录存在
        abs_path = os.path.abspath(filename)
        dir_path = os.path.dirname(abs_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        # 3. 根据格式保存
        encoding = 'utf-8'

        if format == "json":
            with open(filename, 'w', encoding=encoding) as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        elif format == "jsonl":
            data_list = data if isinstance(data, list) else [data]
            with open(filename, "a", encoding=encoding) as f:
                for item in data_list:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")

        elif format == "csv":
            data_list = data if isinstance(data, list) else [data]
            if data_list:
                keys = data_list[0].keys()
                mode = 'a' if os.path.exists(filename) else 'w'
                with open(filename, mode, newline='', encoding='utf-8-sig') as f:
                    writer = csv.DictWriter(f, fieldnames=keys)
                    if mode == 'w':
                        writer.writeheader()
                    writer.writerows(data_list)

        else:
            logger.error(f"❌ [Toolbox] Unknown format: {format}")
            return False

        logger.info(f"✅ [Toolbox] Data saved successfully: {filename}")
        return True

    except (IOError, KeyError, TypeError) as e:
        logger.error(f"❌ [Toolbox] Save Error: {e}")
        return False

# 7. 📧 Notification (Mock)


def notify(msg: str, title: str = "AutoWeb Notification"):
    """
    [Notify] 发送通知 (目前只打印，未来可对接 Email/Slack)
    """
    logger.info(f"\n🔔 [{title}] {msg}\n")
    return True
