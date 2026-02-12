# ==============================================================================
# Code Cache Manager - 代码缓存复用系统
# ==============================================================================
# 核心功能：
# 1. 将成功执行的代码存入 Milvus 向量库
# 2. 根据任务描述 + DOM 结构检索相似代码
# 3. 复用历史代码，减少 Token 消耗
# ==============================================================================

import hashlib
import re
import atexit
from typing import List, Dict, Any, Optional, NamedTuple
from datetime import datetime
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

from langchain_milvus import Milvus
from langchain_core.documents import Document

from config import CODE_COLLECTION_NAME


class CacheHit(NamedTuple):
    """缓存命中结果"""
    id: str
    code: str
    score: float
    url_pattern: str
    goal: str  # [V4] 改为 goal
    success_count: int
    user_task: str = ""  # [V5] 原始用户任务


# ==============================================================================
# [V5] 参数 Diff + 替换工具函数
# ==============================================================================

def extract_param_diffs(cached_task: str, current_task: str) -> list:
    """
    对比两个 task，提取变化的"参数"部分。

    使用 token 级 SequenceMatcher diff：
    1. 先用正则将文本切分为 token（英文单词/数字保持完整，其余逐字符）
    2. 对 token 序列做 diff，提取 replace 操作
    3. 将替换的 token 组拼回字符串，作为参数差异

    能正确处理中文、混合语言等无空格文本。
    按旧参数长度降序排列（防止短串误替换长串的子串）。
    """
    import difflib
    import re as _re

    def _tokenize(text: str) -> list:
        """连续英文/数字为一个 token，其余每个非空字符为一个 token"""
        return _re.findall(r'[a-zA-Z0-9_]+|\S', text)

    old_tokens = _tokenize(cached_task)
    new_tokens = _tokenize(current_task)

    matcher = difflib.SequenceMatcher(None, old_tokens, new_tokens)
    diffs = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'replace':
            old_val = ''.join(old_tokens[i1:i2])
            new_val = ''.join(new_tokens[j1:j2])
            if len(old_val) >= 2 and len(new_val) >= 2:
                diffs.append((old_val, new_val))

    # 按旧参数长度降序排列，防止 "fish" 在 "fishery" 之前被替换
    diffs.sort(key=lambda x: len(x[0]), reverse=True)
    return diffs


def apply_param_substitution(code: str, diffs: list) -> str:
    """
    在代码的字符串字面量中替换参数（零 LLM Token）
    只替换引号内的内容，避免误改变量名/函数名
    """
    import re as _re
    for old_val, new_val in diffs:
        # 匹配单引号或双引号内包含 old_val 的字符串
        pattern = _re.compile(
            r"""(['"])([^'"]*?)""" + _re.escape(old_val) + r"""([^'"]*?)\1"""
        )
        code = pattern.sub(
            lambda m: f"{m.group(1)}{m.group(2)}{new_val}{m.group(3)}{m.group(1)}",
            code
        )
    return code


class CodeCacheManager:
    """
    代码缓存管理器

    存储策略：
    - 仅存储验证通过的代码
    - 向量化: goal + url_pattern + dom_skeleton[:2500]
    - 辅助匹配: url_pattern + dom_hash
    """

    SIMILARITY_THRESHOLD = 0.9
    DOM_MAX_LENGTH = 2500
    MAX_EMBEDDING_CHARS = 4000  # [V4] Embedding 输入最大字符数
    MAX_CODE_WARN = 4000  # [V4] 代码超过此长度输出警告

    def __init__(self):
        self._vector_store: Optional[Milvus] = None
        self._embeddings = None
        # [V5] 异步存储线程池（单线程保证顺序）
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="CodeCache")
        # 程序退出时等待任务完成
        atexit.register(self._shutdown)

    def _get_embeddings(self):
        """懒加载 Embedding 模型"""
        if self._embeddings is None:
            from rag.retriever_qa import get_embedding_model
            self._embeddings = get_embedding_model()
        return self._embeddings

    def _get_vector_store(self) -> Milvus:
        """懒加载 Milvus 连接"""
        if self._vector_store is None:
            from config import MILVUS_URI

            # 使用 COSINE 相似度（返回值范围 0~1，越大越相似）
            index_params = {
                "metric_type": "COSINE",
                "index_type": "AUTOINDEX",
            }

            self._vector_store = Milvus(
                embedding_function=self._get_embeddings(),
                connection_args={"uri": MILVUS_URI},
                collection_name=CODE_COLLECTION_NAME,
                index_params=index_params,
                consistency_level="Bounded",
                auto_id=True,
                enable_dynamic_field=True,  # [V5] 启用动态字段，允许存储 user_task 等新字段
            )
        return self._vector_store

    # ========== 辅助方法 ==========

    def _normalize_url(self, url: str) -> str:
        """
        URL 归一化：提取域名 + 路径模式，去除动态参数

        Example:
            https://item.taobao.com/item.htm?id=123&spm=xxx
            -> taobao.com/item.htm
        """
        try:
            parsed = urlparse(url)
            # 提取主域名 (去掉 www. 和子域名)
            domain_parts = parsed.netloc.split('.')
            if len(domain_parts) >= 2:
                domain = '.'.join(domain_parts[-2:])
            else:
                domain = parsed.netloc

            # 清理路径：去除数字 ID，保留结构
            path = parsed.path
            # 将连续数字替换为 *
            path = re.sub(r'/\d+', '/*', path)

            return f"{domain}{path}"
        except Exception:
            return url

    def _compute_dom_hash(self, dom_skeleton: str) -> str:
        """计算 DOM 结构哈希"""
        # 使用前 2500 字符计算 MD5
        content = dom_skeleton[:self.DOM_MAX_LENGTH] if dom_skeleton else ""
        return hashlib.md5(content.encode('utf-8')).hexdigest()[:16]

    def _build_embedding_text(self, goal: str, url: str, user_task: str = "", locator_info: str = "") -> str:
        """构建用于向量化的文本 [V5] 移除 DOM，改用 Task + Goal + URL + Locator 摘要"""
        url_pattern = self._normalize_url(url)

        parts = []
        if user_task:
            parts.append(f"Task: {user_task}")
        parts.append(f"Goal: {goal}" * 5)
        parts.append(f"URL: {url_pattern}")
        if locator_info:
            parts.append(f"Locators: {locator_info[:800]}")
        text = "\n".join(parts)

        # 截断保护
        if len(text) > self.MAX_EMBEDDING_CHARS:
            text = text[:self.MAX_EMBEDDING_CHARS]
            print(
                f"⚠️ [CodeCache] Embedding 输入截断至 {self.MAX_EMBEDDING_CHARS} chars")

        return text

    # ========== 核心 API ==========

    def search(
        self,
        user_task: str,
        goal: str,
        url: str,
        locator_info: str = "",
        top_k: int = 3
    ) -> List[CacheHit]:
        """
        检索相似代码

        Args:
            task: 用户任务描述
            url: 当前页面 URL
            locator_info: Observer 的定位策略摘要
            top_k: 返回数量

        Returns:
            List[CacheHit]: 按相似度排序的缓存命中列表
        """
        print(f"🔍 [CodeCache] Searching for similar code...")

        try:
            vector_store = self._get_vector_store()

            # 构建检索文本
            query_text = self._build_embedding_text(
                goal, url, user_task, locator_info)

            # 向量检索
            results = vector_store.similarity_search_with_score(
                query=query_text,
                k=top_k
            )

            hits = []
            for doc, score in results:
                # COSINE 相似度：score 范围 0~1，越大越相似
                similarity = score

                if similarity >= self.SIMILARITY_THRESHOLD:
                    hit = CacheHit(
                        id=doc.metadata.get("cache_id", ""),
                        code=doc.metadata.get("code", ""),
                        score=similarity,
                        url_pattern=doc.metadata.get("url_pattern", ""),
                        goal=doc.metadata.get("goal", ""),
                        success_count=doc.metadata.get("success_count", 0),
                        user_task=doc.metadata.get("user_task", ""),  # [V5]
                    )
                    hits.append(hit)

            if hits:
                print(
                    f"✅ Found {len(hits)} cache hits (best score: {hits[0].score:.4f})")
            else:
                print(
                    f"❌ No cache hit above threshold ({self.SIMILARITY_THRESHOLD})")

            return hits

        except Exception as e:
            print(f"⚠️ [CodeCache] Search error: {e}")
            return []

    # 导航类代码的最大长度阈值（超过此长度认为不是纯导航代码）
    NAVIGATION_CODE_MAX_LENGTH = 200

    # 去重相似度阈值（存储前检查）
    DUPLICATE_THRESHOLD = 0.90

    def _is_navigation_task(self, goal: str, code: str) -> bool:
        """
        判断是否为纯导航/跳转类代码（应跳过存储）

        判断标准：代码很短 且 主要是 tab.get() 调用
        """
        # 代码较长，不可能是纯导航
        if len(code) > self.NAVIGATION_CODE_MAX_LENGTH:
            return False

        # 检查代码内容：如果主要是 tab.get() 调用
        code_lower = code.lower().strip()
        navigation_patterns = ["tab.get(", "tab.get ("]

        for pattern in navigation_patterns:
            if pattern in code_lower:
                # 统计代码行数（去掉空行和 print）
                meaningful_lines = [
                    line for line in code.split('\n')
                    if line.strip() and not line.strip().startswith('print')
                ]
                # 如果有意义的代码行 <= 3 行，认为是纯导航
                if len(meaningful_lines) <= 3:
                    return True

        return False

    def _is_duplicate(self, goal: str, dom_skeleton: str, url: str) -> bool:
        """检查是否与已存储内容重复（相似度 >= 90%）"""
        try:
            vector_store = self._get_vector_store()
            query_text = self._build_embedding_text(goal, dom_skeleton, url)

            results = vector_store.similarity_search_with_score(
                query=query_text, k=1)

            if results:
                _, score = results[0]
                if score >= self.DUPLICATE_THRESHOLD:
                    print(
                        f"   ⚠️ [CodeCache] 相似内容已存在 (score={score:.4f} >= {self.DUPLICATE_THRESHOLD})，跳过存储")
                    return True
            return False
        except Exception as e:
            print(f"⚠️ [CodeCache] Duplicate check error: {e}")
            return False  # 检查失败时允许存储

    def _shutdown(self):
        """关闭线程池，等待任务完成"""
        print("🔄 [CodeCache] 等待后台存储任务完成...")
        self._executor.shutdown(wait=True)
        print("✅ [CodeCache] 后台任务已完成")

    def _do_save_async(self, goal: str, dom_skeleton: str, url: str, code: str, user_task: str = "", locator_info: str = ""):
        """
        后台执行的存储逻辑（在线程池中运行）
        包含：去重检查 + 实际存储
        """
        try:
            # 去重检查（耗时操作，现在在后台执行）
            if self._is_duplicate(goal, dom_skeleton, url):
                return

            vector_store = self._get_vector_store()

            # 构建元数据
            url_pattern = self._normalize_url(url)
            dom_hash = self._compute_dom_hash(dom_skeleton)
            cache_id = f"{dom_hash}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

            metadata = {
                "cache_id": cache_id,
                "url_pattern": url_pattern,
                "dom_hash": dom_hash,
                "goal": goal,
                "user_task": user_task,  # [V5] 存储原始用户任务
                "code": code,
                "code_length": len(code),
                "success_count": 1,
                "fail_count": 0,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }

            # 构建向量化文本（不再使用 DOM，改用 locator_info）
            embedding_text = self._build_embedding_text(
                goal, url, user_task=user_task, locator_info=locator_info)

            # 创建 Document 并存储
            doc = Document(page_content=embedding_text, metadata=metadata)
            vector_store.add_documents([doc])

            print(f"   ✅ [CodeCache] 后台存储完成: {cache_id}")

        except Exception as e:
            print(f"❌ [CodeCache] 后台存储失败: {e}")

    def save(
        self,
        goal: str,
        dom_skeleton: str,
        url: str,
        code: str,
        user_task: str = "",
        locator_info: str = ""
    ) -> None:
        """
        异步存储成功执行的代码（非阻塞）

        Args:
            goal: 当前步骤目标
            dom_skeleton: DOM 骨架（仅用于去重 hash）
            url: 当前页面 URL
            code: 生成的代码
            user_task: 原始用户任务（用于参数感知复用）
            locator_info: Observer 的定位策略摘要（用于 embedding）

        Note:
            此方法立即返回，实际存储在后台线程执行
        """
        # ========== 同步过滤（轻量级，立即执行）==========

        # 过滤: 跳过纯导航类代码（短代码 + 只有 tab.get）
        if self._is_navigation_task(goal, code):
            print(f"⏭️ [CodeCache] 跳过纯导航代码 ({len(code)} chars)")
            return False

        # 超长代码警告
        if len(code) > self.MAX_CODE_WARN:
            print(f"⚠️ [CodeCache] 代码较长 ({len(code)} chars)，建议 Planner 拆分任务")

        # ========== 异步存储（提交到后台线程）==========
        print(f"📤 [CodeCache] 提交后台存储任务 (code: {len(code)} chars)")
        self._executor.submit(self._do_save_async, goal,
                              dom_skeleton, url, code, user_task, locator_info)
        return True

    def update_stats(self, cache_id: str, success: bool) -> bool:
        """
        更新执行统计

        注意：Milvus 不支持直接更新，需要删除后重新插入
        这里简化处理，只打印日志
        """
        action = "success" if success else "fail"
        print(f"📊 [CodeCache] Recording {action} for cache_id: {cache_id}")
        # TODO: 实现真正的统计更新 (需要读取 -> 修改 -> 重新插入)
        return True


# 单例模式
code_cache_manager = CodeCacheManager()
