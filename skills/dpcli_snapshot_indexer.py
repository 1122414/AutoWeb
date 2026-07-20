"""
dp_cli Snapshot Indexer - 快照索引构建器

职责：
- 从 raw snapshot data 构建可搜索索引
- 建立 lookup_manifest (by_ref, by_role, by_text, by_region, by_structural_group)
- 生成 summary 统计信息
- 构建 compressed_index (压缩分组索引)

三层信息架构中 Layer 2 的构建器: 中等结构化信息，可搜索、可展开。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Set

from skills.logger import logger


class SnapshotIndexer:
    """
    从 dp_cli 快照原始数据构建搜索索引

    输入: dp_cli executor 返回的完整 snapshot JSON
    输出: index (searchable dict) + compressed_index (grouped by structural hash)
    """

    def __init__(self):
        self._ref_cache: Dict[str, Dict[str, Any]] = {}

    # ─── 主入口 ─────────────────────────────────────────────

    def build_index(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """构建可搜索索引"""
        data = snapshot.get("data") or {}
        idx = data.get("index") or {}
        if not isinstance(idx, dict):
            idx = {}

        interactables = idx.get("interactable_elements") or []
        surface = idx.get("surface_index") or []
        deep = idx.get("deep_index") or []
        regions = idx.get("data_regions") or []
        tree = idx.get("tree") or {}
        stats = idx.get("stats") or {}

        all_nodes_raw = list(interactables) + list(surface) + list(deep)
        self._ref_cache = {}
        nodes_without_ref: List[Dict[str, Any]] = []
        for n in all_nodes_raw:
            if not isinstance(n, dict):
                continue
            ref = (n or {}).get("ref")
            if not ref:
                nodes_without_ref.append(dict(n))
                continue
            ref_str = str(ref)
            existing = self._ref_cache.get(ref_str)
            if existing:
                merged = self._merge_node_info(existing, n)
                self._ref_cache[ref_str] = merged
            else:
                self._ref_cache[ref_str] = dict(n)
        all_nodes = nodes_without_ref + list(self._ref_cache.values())

        return {
            "snapshot_id": self._extract_snapshot_id(data),
            "by_ref": self._build_by_ref(all_nodes),
            "by_role": self._group_by(all_nodes, "role"),
            "by_text": self._build_text_index(all_nodes),
            "by_region": self._build_region_index(regions, tree),
            "by_parent": self._build_parent_index(all_nodes, tree),
            "by_tag": self._group_by(all_nodes, "tag"),
            "tree": tree,
            "summary": self._build_summary(all_nodes, regions, stats),
            "regions": regions,
        }

    def build_compressed_index(
        self,
        nodes: List[Dict[str, Any]],
        min_group_size: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        构建压缩分组索引（相似兄弟节点合并）

        按 parent_ref 分组后，对每个组内的 siblings 做 structural hash 分组。
        """
        deduplicated: Dict[str, Dict[str, Any]] = {}
        nodes_without_ref: List[Dict[str, Any]] = []
        for n in nodes:
            if not isinstance(n, dict):
                continue
            ref = str(n.get("ref") or "")
            if not ref:
                nodes_without_ref.append(dict(n))
                continue
            existing = deduplicated.get(ref)
            deduplicated[ref] = (
                self._merge_node_info(existing, n)
                if existing
                else dict(n)
            )

        unique_nodes = nodes_without_ref + list(deduplicated.values())
        by_parent: Dict[str, List[Dict[str, Any]]] = {}
        for n in unique_nodes:
            parent = str(n.get("parent_ref") or "__root__")
            by_parent.setdefault(parent, []).append(n)

        compressed_groups: List[Dict[str, Any]] = []
        processed_refs: Set[str] = set()

        for parent_ref, siblings in by_parent.items():
            if len(siblings) < min_group_size:
                continue
            groups = self._group_by_structural_hash(siblings)
            group_idx = 0
            for shash, group in groups.items():
                if len(group) < min_group_size:
                    continue
                group_idx += 1
                compressed = self._build_compressed_group(
                    group, shash, parent_ref, group_idx
                )
                compressed_groups.append(compressed)
                for n in group:
                    processed_refs.add(n.get("ref", ""))

        # 未被压缩的节点保留
        uncompressed = [n for n in unique_nodes if n.get("ref") not in processed_refs]

        logger.info(
            f"   🗜️  [Indexer] 压缩: {len(compressed_groups)} groups, "
            f"{len(uncompressed)} uncompressed (total {len(unique_nodes)} nodes)"
        )
        return compressed_groups

    # ─── 索引构建器 ─────────────────────────────────────────

    @staticmethod
    def _build_by_ref(nodes: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        for n in nodes:
            ref = (n or {}).get("ref")
            if ref:
                result[str(ref)] = n
        return result

    @staticmethod
    def _group_by(nodes: List[Dict[str, Any]], key: str) -> Dict[str, List[Dict[str, Any]]]:
        result: Dict[str, List[Dict[str, Any]]] = {}
        for n in nodes:
            val = str((n or {}).get(key) or "").strip().lower()
            if val:
                result.setdefault(val, []).append(n)
        return result

    @staticmethod
    def _build_text_index(nodes: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """
        构建文本倒排索引
        将 name/text/placeholder/label/aria_label/alt 分词后建立索引
        """
        result: Dict[str, List[Dict[str, Any]]] = {}
        text_fields = ["name", "text", "placeholder", "label", "input_type", "value"]
        for n in nodes:
            if not isinstance(n, dict):
                continue
            ref = n.get("ref")
            for field in text_fields:
                val = str(n.get(field) or "").strip()
                if not val:
                    continue
                for token in val.split():
                    token = token.lower().strip(".,;:!?\"'()[]{}")
                    if len(token) < 2:
                        continue
                    entry = {"ref": ref, "field": field, "text": val}
                    result.setdefault(token, []).append(entry)
        return result

    @staticmethod
    def _build_region_index(
        regions: List[Dict[str, Any]], tree: Dict[str, Any]
    ) -> Dict[str, List[str]]:
        """
        构建 region → refs 映射
        通过 tree.children_map 找到每个 region 下的元素
        """
        children_map = tree.get("children_map") or {}
        result: Dict[str, List[str]] = {}
        for r in regions:
            ref = (r or {}).get("ref")
            if not ref:
                continue
            refs = list(children_map.get(str(ref), []))
            result[str(ref)] = refs
        return result

    @staticmethod
    def _build_parent_index(
        nodes: List[Dict[str, Any]], tree: Dict[str, Any]
    ) -> Dict[str, List[str]]:
        """构建 parent_ref → children_refs 映射"""
        children_map = tree.get("children_map") or {}
        result: Dict[str, List[str]] = {}
        for parent, children in children_map.items():
            result[str(parent)] = list(children)
        return result

    # ─── 统计 ────────────────────────────────────────────────

    @staticmethod
    def _build_summary(
        nodes: List[Dict[str, Any]],
        regions: List[Dict[str, Any]],
        stats: Dict[str, Any],
    ) -> Dict[str, Any]:
        elements = [n for n in nodes if (n or {}).get("ref_type") != "container"]
        containers = [n for n in nodes if (n or {}).get("ref_type") == "container"]
        buttons = [n for n in nodes if (n or {}).get("role") in ("button",) or (n or {}).get("tag") == "button"]
        links = [n for n in nodes if (n or {}).get("role") == "link" or (n or {}).get("tag") == "a"]
        inputs = [n for n in nodes if (n or {}).get("ref_type") == "element" and (
            (n or {}).get("role") in ("textbox", "searchbox", "combobox") or
            (n or {}).get("tag") in ("input", "textarea", "select") or
            (n or {}).get("input_type")
        )]
        return {
            "elements": len(elements),
            "containers": len(containers),
            "regions": len(regions),
            "buttons": len(buttons),
            "links": len(links),
            "inputs": len(inputs),
            "total_nodes": stats.get("total_nodes", len(nodes)),
            "interactable_now": stats.get("interactable_now", 0),
            "in_viewport": stats.get("in_viewport", 0),
            "offscreen": stats.get("offscreen", 0),
        }

    # ─── 结构压缩 ────────────────────────────────────────────

    @staticmethod
    def _compute_structural_hash(node: Dict[str, Any]) -> str:
        parts = [
            node.get("tag", ""),
            node.get("role", ""),
            node.get("input_type", ""),
            node.get("ref_type", ""),
            node.get("parent_ref", ""),
        ]
        raw = "_".join(filter(None, parts))
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    @staticmethod
    def _merge_node_info(
        existing: Dict[str, Any], incoming: Dict[str, Any]
    ) -> Dict[str, Any]:
        merged = dict(existing)
        for key, value in incoming.items():
            if value in (None, "", [], {}):
                continue
            current = merged.get(key)
            if isinstance(current, dict) and isinstance(value, dict):
                merged[key] = {
                    **current,
                    **{
                        nested_key: nested_value
                        for nested_key, nested_value in value.items()
                        if nested_value not in (None, "", [], {})
                    },
                }
                continue
            if current in (None, "", [], {}):
                merged[key] = value

        ref = str(merged.get("ref") or "")
        if not merged.get("ref_type"):
            if ref.startswith("e"):
                merged["ref_type"] = "element"
            elif ref.startswith("r"):
                merged["ref_type"] = "container"
        return merged

    def _group_by_structural_hash(
        self, siblings: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """将兄弟节点按结构 hash 分组"""
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for n in siblings:
            shash = self._compute_structural_hash(n)
            groups.setdefault(shash, []).append(n)
        return groups

    def _build_compressed_group(
        self, group: List[Dict[str, Any]], shash: str, parent_ref: str, idx: int
    ) -> Dict[str, Any]:
        """
        构建压缩分组输出

        输出结构:
        {
            group_id, type, kind,
            count,
            template: { role_pattern, region_ref },
            data: { text: [], href: [], _ref: [], _index: [] },
            samples: [{ref, text, role}, ...],
            available_actions: [...]
        }
        """
        template = group[0]
        tag = template.get("tag", "unknown")
        role = template.get("role", "unknown")
        group_id = f"g_{tag}_{shash}"
        count = len(group)

        # 提取数据列
        data: Dict[str, List[Any]] = {}
        data["_ref"] = [n.get("ref", "") for n in group]
        data["_index"] = [self._extract_ref_index(n.get("ref", "")) for n in group]
        for key in ("text", "name", "href", "role", "tag"):
            col = [n.get(key, "") for n in group]
            if any(col):
                data[key] = col

        # samples (最多 5 个)
        samples = []
        for n in group[:5]:
            s: Dict[str, Any] = {"ref": n.get("ref", "")}
            for f in ("text", "name", "role", "tag"):
                v = n.get(f)
                if v:
                    s[f] = v
            samples.append(s)

        # available_actions
        actions = []
        ref_type = template.get("ref_type")
        if ref_type == "container":
            actions.append("expand")
        if count >= 3:
            actions.append("list-items")
        if role in ("link", "button"):
            actions.append("click")

        return {
            "group_id": group_id,
            "type": "compressed_ref_group",
            "kind": "repeated_structure",
            "count": count,
            "template": {
                "role_pattern": [role],
                "tag": tag,
                "parent_ref": parent_ref,
            },
            "data": data,
            "samples": samples,
            "available_actions": actions or ["extract"],
        }

    # ─── 工具方法 ────────────────────────────────────────────

    @staticmethod
    def _extract_ref_index(ref: str) -> int:
        import re
        m = re.search(r'(\d+)$', str(ref))
        return int(m.group(1)) if m else -1

    @staticmethod
    def _extract_snapshot_id(data: Dict[str, Any]) -> str:
        identity = data.get("page_identity") or {}
        return str(identity.get("snapshot_id") or identity.get("page_id") or "")

    def lookup(self, ref: str) -> Optional[Dict[str, Any]]:
        return self._ref_cache.get(ref)
