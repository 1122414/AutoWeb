"""
dp_cli Snapshot Query Engine - 快照本地查询引擎

职责：
- 为 TargetSelector 提供本地查询能力（不调用 LLM）
- search_snapshot: 多字段组合查询
- get_ref / get_region: 精确查找
- expand_group: 展开压缩分组
- find_by_text / find_near: 文本/位置查询
- verify_ref: 验证 ref 有效性

所有查询操作均在本地 index 上完成，不读取完整 index 喂给 LLM。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from skills.dpcli_snapshot_store import SnapshotStore
from skills.dpcli_snapshot_indexer import SnapshotIndexer
from skills.logger import logger


class SnapshotQueryEngine:
    """
    快照本地查询引擎

    使用本地构建的 index 进行查询，结果返回小候选包（1-8 个），
    不把全量 index 暴露给 LLM。
    """

    def __init__(self, store: Optional[SnapshotStore] = None):
        self._store = store or SnapshotStore()
        self._index: Dict[str, Any] = {}
        self._compressed: List[Dict[str, Any]] = []
        self._full_snapshot: Optional[Dict[str, Any]] = None
        self._by_ref: Dict[str, Dict[str, Any]] = {}
        self._by_role: Dict[str, List[Dict[str, Any]]] = {}
        self._by_text: Dict[str, List[Dict[str, Any]]] = {}
        self._by_region: Dict[str, List[str]] = {}
        self._by_parent: Dict[str, List[str]] = {}
        self._tree: Dict[str, Any] = {}

    # ─── 初始化 ─────────────────────────────────────────────

    def load(self, snapshot_id: str) -> bool:
        """从磁盘加载索引"""
        full = self._store.load_full(snapshot_id)
        index = self._store.load_index(snapshot_id)
        compressed_data = self._store.load_compressed_index(snapshot_id)

        if not index:
            logger.warning(f"   ⚠️ [QueryEngine] 未找到 index: {snapshot_id}")
            return False

        self._full_snapshot = full
        self._index = index
        self._compressed = (compressed_data or {}).get("groups", []) if isinstance(compressed_data, dict) else []
        self._by_ref = index.get("by_ref", {})
        self._by_role = index.get("by_role", {})
        self._by_text = index.get("by_text", {})
        self._by_region = index.get("by_region", {})
        self._by_parent = index.get("by_parent", {})
        self._tree = index.get("tree", {})
        return True

    def load_from_ref(self, snapshot_ref: Dict[str, Any]) -> bool:
        """从 dpcli_snapshot_ref 加载"""
        sid = snapshot_ref.get("snapshot_id", "")
        if sid:
            return self.load(sid)
        return False

    # ─── 查询 API ───────────────────────────────────────────

    def search_snapshot(
        self,
        query: Dict[str, Any],
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        多字段组合查询

        query 可包含:
        - role: str | List[str]  角色过滤
        - tag: str | List[str]   标签过滤
        - text: str              文本包含
        - name: str              name 精确匹配
        - ref_type: str          "element" | "container"
        - visible: bool          只返回可见
        - interactable: bool     只返回可交互
        - parent_ref: str        按父级过滤
        - region_ref: str        按 region 过滤
        """
        candidates = list(self._by_ref.values())

        role_filter = query.get("role")
        if role_filter:
            if isinstance(role_filter, str):
                role_filter = [role_filter]
            role_set = {r.lower() for r in role_filter}
            candidates = [n for n in candidates if str(n.get("role", "")).lower() in role_set]

        tag_filter = query.get("tag")
        if tag_filter:
            if isinstance(tag_filter, str):
                tag_filter = [tag_filter]
            tag_set = {t.lower() for t in tag_filter}
            candidates = [n for n in candidates if str(n.get("tag", "")).lower() in tag_set]

        text_filter = query.get("text")
        if text_filter:
            candidates = self._filter_by_text(candidates, str(text_filter))

        name_filter = query.get("name")
        if name_filter:
            name_lower = str(name_filter).lower()
            candidates = [n for n in candidates if str(n.get("name", "").lower()) == name_lower]

        ref_type_filter = query.get("ref_type")
        if ref_type_filter:
            candidates = [n for n in candidates if n.get("ref_type") == ref_type_filter]

        parent_filter = query.get("parent_ref")
        if parent_filter:
            parent_refs = self._by_parent.get(str(parent_filter), [])
            parent_set = set(parent_refs)
            candidates = [n for n in candidates if n.get("ref") in parent_set]

        region_filter = query.get("region_ref")
        if region_filter:
            region_refs = self._by_region.get(str(region_filter), [])
            region_set = set(region_refs)
            candidates = [n for n in candidates if n.get("ref") in region_set]

        if query.get("visible") is True:
            candidates = [n for n in candidates if n.get("in_viewport") is True or n.get("visible") is True]
        if query.get("interactable") is True:
            candidates = [n for n in candidates if n.get("interactable_now") is True]

        return candidates[:limit]

    def get_ref(self, ref: str) -> Optional[Dict[str, Any]]:
        """精确查找 ref"""
        return self._by_ref.get(str(ref))

    def get_region(self, region_ref: str) -> Optional[Dict[str, Any]]:
        """查找 region"""
        regions = self._index.get("regions", [])
        for r in regions:
            if r.get("ref") == str(region_ref):
                return r
        return None

    def expand_group(self, group_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """展开压缩分组"""
        for g in self._compressed:
            if g.get("group_id") == group_id:
                refs = g.get("data", {}).get("_ref", [])[:limit]
                return [self._by_ref.get(r) for r in refs if self._by_ref.get(r)]
        return []

    def find_by_text(
        self, text: str, scope: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        text_lower = text.lower()
        candidates: Dict[str, Dict[str, Any]] = {}
        for token in text_lower.split():
            if token in self._by_text:
                for entry in self._by_text[token]:
                    ref = entry.get("ref", "")
                    if ref not in candidates:
                        candidates[ref] = self._by_ref.get(ref, {})
                        candidates[ref]["_match_field"] = entry.get("field", "")
                        candidates[ref]["_match_text"] = entry.get("text", "")

        results = list(candidates.values())
        if not results and len(text.strip()) >= 2:
            results = self._substring_search(text_lower)

        if scope:
            results = self._apply_scope(results, scope)
        return results[:20]

    def find_near(
        self, ref_or_text: str, query: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """查找目标 ref 附近的元素"""
        target_ref = ref_or_text
        if not self._by_ref.get(ref_or_text):
            found = self.find_by_text(ref_or_text)
            if found:
                target_ref = found[0].get("ref", "")
            else:
                return []

        parent = self._by_ref.get(target_ref, {}).get("parent_ref", "")
        if not parent:
            return []

        siblings = self._by_parent.get(str(parent), [])
        results = self.search_snapshot({**query, "parent_ref": parent}, limit=10)
        return results

    def load_subtree(self, ref: str, depth: int = 2) -> Dict[str, Any]:
        """加载 ref 子树"""
        node = self._by_ref.get(ref)
        if not node:
            return {"error": f"ref not found: {ref}"}

        result = {"node": node, "children": []}
        if depth <= 0:
            return result

        children_refs = self._by_parent.get(ref, [])
        for cr in children_refs:
            child_result = self.load_subtree(cr, depth - 1)
            result["children"].append(child_result)
        return result

    def verify_ref(self, ref: str, intent: str = "click") -> Dict[str, Any]:
        """验证 ref 有效性"""
        node = self._by_ref.get(ref)
        if not node:
            return {"valid": False, "reason": f"ref {ref} not found in current snapshot"}

        ref_type = node.get("ref_type", "")
        role = node.get("role", "")
        tag = node.get("tag", "")

        issues = []
        if intent in ("click", "type") and ref_type != "element":
            issues.append(f"intent '{intent}' expects element but ref_type is '{ref_type}'")
        if intent == "click":
            if role not in ("button", "link", "tab", "option", "menuitem"):
                if tag not in ("button", "a"):
                    issues.append(f"ref {ref} is not a clickable element (role={role}, tag={tag})")

        return {
            "valid": len(issues) == 0,
            "ref": ref,
            "ref_type": ref_type,
            "role": role,
            "tag": tag,
            "name": node.get("name", ""),
            "text": node.get("text", ""),
            "issues": issues,
            "in_snapshot": bool(self._full_snapshot),
        }

    def get_region_refs(self, region_ref: str) -> List[str]:
        """获取 region 下的所有 ref"""
        return self._by_region.get(str(region_ref), [])

    def search_compressed_groups(self, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        """搜索压缩分组"""
        if kind:
            return [g for g in self._compressed if g.get("kind") == kind]
        return list(self._compressed)

    # ─── 内部工具 ────────────────────────────────────────────

    def _substring_search(self, text_lower: str) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for ref, node in self._by_ref.items():
            for field in ("name", "text", "placeholder", "label"):
                val = str(node.get(field, "")).lower()
                if text_lower in val:
                    node["_match_field"] = field
                    node["_match_text"] = str(node.get(field, ""))
                    results.append(node)
                    break
        return results[:20]

    @staticmethod
    def _filter_by_text(candidates: List[Dict[str, Any]], text: str) -> List[Dict[str, Any]]:
        text_lower = text.lower()
        return [
            n for n in candidates
            if text_lower in str(n.get("text", "")).lower()
            or text_lower in str(n.get("name", "")).lower()
            or text_lower in str(n.get("placeholder", "")).lower()
        ]

    def _apply_scope(
        self, candidates: List[Dict[str, Any]], scope: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        parent = scope.get("parent_ref")
        if parent:
            parent_refs = set(self._by_parent.get(str(parent), []))
            candidates = [n for n in candidates if n.get("ref") in parent_refs]
        region = scope.get("region_ref")
        if region:
            region_refs = set(self._by_region.get(str(region), []))
            candidates = [n for n in candidates if n.get("ref") in region_refs]
        return candidates

    @property
    def is_loaded(self) -> bool:
        return bool(self._by_ref)
