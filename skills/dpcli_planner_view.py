"""
dp_cli Planner View Generator - 规划器视角视图生成

职责：
- 从 snapshot + index 生成 lpcelest_agent_view (Lossy Planner View)
- 生成 capability_map (search, navigation, forms, data_regions, pagination, dialogs)
- 检测 top_level_groups
- 生成 coverage / omitted summary
- 生成 dpcli_observer_diagnostics

三层信息架构中的 Layer 1: 低 token、广覆盖、语义化页面能力地图。
给 Planner 看，帮助 Planner 决定下一步 intent。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from skills.logger import logger


class PlannerViewGenerator:
    """
    从 dp_cli snapshot 生成 lossy planner view

    分区完全基于确定性规则 (不依赖 LLM):
    - search_area: role=search / input+button 组合
    - data_region: 来自 snapshot index.data_regions
    - pagination: next/prev/page number 控件
    - form: role=form / input 集群
    - navigation: role=navigation / nav tag / 链接集群
    - dialog: role=dialog / 模态框检测
    """

    SEARCH_KEYWORDS = {"搜索", "search", "Search", "検索", "查找", "keyword", "关键词"}
    PAGINATION_NEXT = {"下一页", "下一頁", "next", "Next", ">", "→", "›", "》", "next page", "forward"}
    PAGINATION_PREV = {"上一页", "上一頁", "prev", "previous", "Previous", "<", "←", "‹", "《", "prev page", "back"}
    NAV_KEYWORDS = {"首页", "主页", "home", "Home", "index", "Index", "关于", "about", "About", "联系", "contact", "Contact"}

    def __init__(self):
        self._surface_nodes: List[Dict[str, Any]] = []
        self._deep_nodes: List[Dict[str, Any]] = []
        self._interactables: List[Dict[str, Any]] = []
        self._regions: List[Dict[str, Any]] = []
        self._tree: Dict[str, Any] = {}
        self._stats: Dict[str, Any] = {}

    # ─── 主入口 ─────────────────────────────────────────────

    def generate(
        self,
        snapshot: Dict[str, Any],
        compressed_groups: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """生成 dpcli_agent_view"""
        data = snapshot.get("data") or {}
        idx = data.get("index") or {}

        self._interactables = list(idx.get("interactable_elements") or [])
        self._surface_nodes = list(idx.get("surface_index") or [])
        self._deep_nodes = list(idx.get("deep_index") or [])
        self._regions = list(idx.get("data_regions") or [])
        self._tree = idx.get("tree") or {}
        self._stats = idx.get("stats") or {}
        page = data.get("page") or {}
        identity = data.get("page_identity") or {}

        all_nodes = self._dedupe_nodes(
            self._interactables + self._surface_nodes + self._deep_nodes
        )

        capability_map = self._build_capability_map(all_nodes, compressed_groups or [])
        top_level_groups = self._build_top_level_groups(all_nodes)
        coverage = self._build_coverage(all_nodes, compressed_groups or [])

        return {
            "page": {
                "url": page.get("url", ""),
                "title": page.get("title", ""),
                "domain": identity.get("domain", ""),
                "snapshot_id": identity.get("snapshot_id", ""),
                "snapshot_seq": identity.get("snapshot_seq", 0),
                "page_id": identity.get("page_id", ""),
            },
            "focus": {
                "mode": "unknown",
                "confidence": 0.0,
                "reason": "由 Planner 根据任务上下文推断",
            },
            "capability_map": capability_map,
            "top_level_groups": top_level_groups,
            "coverage": coverage,
            "planner_instructions": [
                "只决定下一步 intent 和 target_hint，不要选择具体 ref。",
                "需要具体元素时交给 TargetSelector 查询 full snapshot。",
            ],
        }

    def generate_diagnostics(
        self, raw_snapshot: Dict[str, Any], compressed_groups: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """生成 dpcli_observer_diagnostics（程序化，不依赖 LLM）"""
        data = raw_snapshot.get("data") or {}
        idx = data.get("index") or {}
        stats = idx.get("stats") or {}
        all_nodes = self._dedupe_nodes(
            (idx.get("interactable_elements") or [])
            + (idx.get("surface_index") or [])
            + (idx.get("deep_index") or [])
        )

        data_regions = idx.get("data_regions") or []
        groups = compressed_groups or []

        return {
            "snapshot_ok": raw_snapshot.get("ok", False),
            "raw_nodes": stats.get("total_nodes", 0),
            "interactables": len(idx.get("interactable_elements") or []),
            "containers": len([n for n in all_nodes if (n or {}).get("ref_type") == "container"]),
            "data_regions_detected": len(data_regions),
            "pagination_groups_detected": sum(
                1 for g in self._build_pagination_groups(all_nodes)
            ),
            "structural_groups_detected": len(groups),
            "planner_view_mode": "coverage_first",
            "compression": {
                "strategy": "structural_sibling_hash",
                "min_group_size": 3,
                "largest_group_count": max((g.get("count", 0) for g in groups), default=0),
                "groups_collapsed": len(groups),
            },
            "coverage": {
                "full_snapshot_preserved": True,
                "planner_view_lossy": True,
                "recoverable_from_full_snapshot": True,
            },
            "uncertainty": {
                "task_focus_unclear": True,
                "ambiguous_regions": [],
                "ambiguous_pagination": False,
            },
            "warnings": [],
        }

    # ─── Capability Map ──────────────────────────────────────

    def _build_capability_map(
        self, all_nodes: List[Dict[str, Any]], compressed_groups: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        return {
            "search": self._detect_search_areas(all_nodes),
            "navigation": self._detect_navigation(all_nodes),
            "forms": self._detect_forms(all_nodes),
            "data_regions": self._format_regions(self._regions),
            "pagination": self._build_pagination_groups(all_nodes),
            "content_regions": self._detect_content_regions(all_nodes),
            "dialogs": self._detect_dialogs(all_nodes),
            "primary_actions": self._detect_primary_actions(all_nodes),
        }

    # ─── 各类型检测器 ────────────────────────────────────────

    def _detect_search_areas(self, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        检测搜索区域:
        - 存在 role=search ancestor
        - 或 form 内有 searchbox/textbox + submit/search button
        - 或 input placeholder/name/text 包含搜索关键词，且附近有 button/link
        """
        results = []
        search_inputs = []
        search_buttons = []

        for n in nodes:
            role = str(n.get("role", "")).lower()
            tag = str(n.get("tag", "")).lower()
            text = str(n.get("text", "") or n.get("name", "") or n.get("placeholder", ""))
            input_type = str(n.get("input_type", "")).lower()

            if role in ("searchbox", "textbox") or (tag == "input" and input_type in ("text", "search")):
                has_keyword = any(kw.lower() in text.lower() for kw in self.SEARCH_KEYWORDS)
                if has_keyword or role == "searchbox":
                    search_inputs.append(n)

            if role == "button" or tag in ("button", "a"):
                if any(kw.lower() in text.lower() for kw in self.SEARCH_KEYWORDS):
                    search_buttons.append(n)

        if search_inputs:
            for si in search_inputs[:3]:
                nearby = [sb for sb in search_buttons if self._same_ancestor(si, sb)][:3]
                results.append({
                    "input_ref": si.get("ref", ""),
                    "input_name": si.get("name") or si.get("placeholder", ""),
                    "nearby_buttons": [b.get("ref") for b in nearby],
                    "kind": "search_area",
                })

        return results

    def _detect_navigation(self, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测导航区域"""
        results = []
        nav_groups: Dict[str, List[Dict[str, Any]]] = {}

        for n in nodes:
            role = str(n.get("role", "")).lower()
            tag = str(n.get("tag", "")).lower()
            parent = str(n.get("parent_ref", ""))
            text = str(n.get("text") or n.get("name") or "")

            if role == "navigation" or tag == "nav":
                results.append({
                    "ref": n.get("ref", ""),
                    "name": n.get("name", ""),
                    "kind": "navigation",
                })
                continue

            if role == "link" or tag == "a":
                if any(kw in text for kw in self.NAV_KEYWORDS):
                    nav_groups.setdefault(parent, []).append(n)

        for parent, links in nav_groups.items():
            if len(links) >= 2:
                results.append({
                    "parent_ref": parent,
                    "links": [l.get("ref") for l in links[:10]],
                    "link_count": len(links),
                    "kind": "navigation_links",
                })

        return results[:5]

    def _detect_forms(self, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测表单"""
        results = []
        form_groups: Dict[str, Dict[str, Any]] = {}

        for n in nodes:
            role = str(n.get("role", "")).lower()
            ref = n.get("ref", "")
            parent = str(n.get("parent_ref", ""))
            tag = str(n.get("tag", "")).lower()

            if role == "form" or tag == "form":
                results.append({
                    "ref": ref,
                    "name": n.get("name") or n.get("aria_label", ""),
                    "kind": "form",
                })
                continue

            if role in ("textbox", "searchbox", "combobox", "checkbox", "radio") or \
               tag in ("input", "textarea", "select"):
                group = form_groups.setdefault(parent, {"parent_ref": parent, "inputs": [], "buttons": []})
                group["inputs"].append({
                    "ref": ref,
                    "role": role,
                    "name": n.get("name") or n.get("placeholder", ""),
                    "input_type": n.get("input_type", ""),
                })

            if role == "button" or tag == "button":
                group = form_groups.setdefault(parent, {"parent_ref": parent, "inputs": [], "buttons": []})
                group["buttons"].append({"ref": ref, "name": n.get("name") or n.get("text", "")})

        for parent, group in form_groups.items():
            if group["inputs"]:
                results.append({
                    "parent_ref": parent,
                    "inputs": group["inputs"][:5],
                    "buttons": group["buttons"][:5],
                    "kind": "form_group",
                })

        return results[:5]

    def _format_regions(self, regions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """格式化 data_regions（已由 dp_cli snapshot index 提供）"""
        formatted = []
        for r in regions[:10]:
            formatted.append({
                "ref": r.get("ref", ""),
                "kind": r.get("kind", "unknown"),
                "item_count": r.get("item_count", 0),
                "name": r.get("name", ""),
                "tag": r.get("tag", ""),
                "source_score": r.get("score", 0),
                "why": r.get("why", ""),
                "samples": r.get("sample_items", [])[:3],
                "available_actions": self._region_actions(r),
            })
        return formatted

    @staticmethod
    def _dedupe_nodes(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        from skills.dpcli_snapshot_indexer import SnapshotIndexer

        merged_by_ref: Dict[str, Dict[str, Any]] = {}
        nodes_without_ref: List[Dict[str, Any]] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            ref = str(node.get("ref") or "")
            if not ref:
                nodes_without_ref.append(dict(node))
                continue
            existing = merged_by_ref.get(ref)
            merged_by_ref[ref] = (
                SnapshotIndexer._merge_node_info(existing, node)
                if existing
                else dict(node)
            )
        return nodes_without_ref + list(merged_by_ref.values())

    @staticmethod
    def _region_actions(region: Dict[str, Any]) -> List[str]:
        kind = region.get("kind", "")
        count = region.get("item_count", 0)
        actions = []
        ref = region.get("ref")
        if ref and (str(ref).startswith("r") or str(ref).startswith("g")):
            actions.append("expand")
        if count >= 3:
            actions.append("list-items")
        if kind in ("list", "table", "card_grid", "repeated_structure"):
            actions.append("extract")
        if kind in ("list", "table", "card_grid"):
            actions.append("batch-detail-extract_candidate")
        return actions

    def _build_pagination_groups(self, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        检测分页控件:
        - role in {button, link} 或 tag in {a, button} 或 interactable_now
        - name/text 包含 next/prev/page number 关键词
        - 同父级下同时存在数字页码和 next/prev
        """
        groups: Dict[str, Dict[str, Any]] = {}
        for n in nodes:
            text = str(n.get("text") or n.get("name") or "")
            if not text.strip():
                continue
            role = str(n.get("role", "")).lower()
            tag = str(n.get("tag", "")).lower()
            ref = n.get("ref", "")
            parent = str(n.get("parent_ref", ""))

            if not self._is_pagination_candidate(n):
                continue

            is_next = any(kw.lower() in text.lower() for kw in self.PAGINATION_NEXT)
            is_prev = any(kw.lower() in text.lower() for kw in self.PAGINATION_PREV)
            is_num = text.strip().isdigit() and 1 <= int(text.strip()) <= 999

            if not (is_next or is_prev or is_num):
                continue

            group = groups.setdefault(parent, {
                "group_id": f"g_pagination_{hash(parent) & 0xFFFF:04x}",
                "controls": [],
                "evidence": [],
                "parent_ref": parent,
            })
            direction = "next" if is_next else "prev" if is_prev else "page_number"
            group["controls"].append({
                "ref": ref,
                "label": text.strip(),
                "direction": direction,
                "enabled": not self._is_disabled(n),
            })

        results = []
        for group in groups.values():
            has_next_or_prev = any(c["direction"] in ("next", "prev") for c in group["controls"])
            has_pages = any(c["direction"] == "page_number" for c in group["controls"])
            if has_next_or_prev or (has_pages and len(group["controls"]) >= 2):
                evidence = []
                if has_next_or_prev:
                    evidence.append("next/prev keyword in text/name")
                if has_pages:
                    evidence.append("sibling numeric page controls")
                group["evidence"] = evidence
                group["available_actions"] = ["click"]
                results.append(group)

        return results[:5]

    def _detect_content_regions(self, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测内容区域（不可交互的文本容器）"""
        results = []
        for n in nodes:
            role = str(n.get("role", "")).lower()
            tag = str(n.get("tag", "")).lower()
            if role in ("main", "article", "section", "region") or tag in ("main", "article", "section"):
                results.append({
                    "ref": n.get("ref", ""),
                    "role": role,
                    "tag": tag,
                    "name": n.get("name", ""),
                    "kind": "content_region",
                })
        return results[:5]

    def _detect_dialogs(self, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测对话框/模态框"""
        results = []
        for n in nodes:
            role = str(n.get("role", "")).lower()
            if role == "dialog":
                results.append({
                    "ref": n.get("ref", ""),
                    "name": n.get("name", ""),
                    "kind": "dialog",
                })
        return results[:3]

    def _detect_primary_actions(self, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """检测主要操作按钮（高可见性按钮/链接）"""
        results = []
        for n in nodes:
            role = str(n.get("role", "")).lower()
            tag = str(n.get("tag", "")).lower()
            text = str(n.get("text") or n.get("name") or "")
            if (role == "button" or tag in ("button", "a")) and len(text) > 0:
                if not any(
                    kw.lower() in text.lower()
                    for kwset in (self.SEARCH_KEYWORDS, self.PAGINATION_NEXT, self.PAGINATION_PREV, self.NAV_KEYWORDS)
                    for kw in kwset
                ):
                    results.append({
                        "ref": n.get("ref", ""),
                        "role": role,
                        "text": text[:40],
                        "kind": "primary_action",
                    })
        return results[:10]

    # ─── Top-Level Groups ────────────────────────────────────

    def _build_top_level_groups(self, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        构建 top_level_groups（不由 LLM 生成，由 detector 合并）
        """
        groups = []

        search_areas = self._detect_search_areas(nodes)
        for sa in search_areas:
            groups.append({
                "group_id": f"g_search_{hash(sa.get('input_ref', '')) & 0xFFFF:04x}",
                "kind": "search_area",
                "count": 1 + len(sa.get("nearby_buttons", [])),
                "refs": [sa.get("input_ref")] + sa.get("nearby_buttons", []),
            })

        pagination = self._build_pagination_groups(nodes)
        for pg in pagination:
            groups.append({
                "group_id": pg.get("group_id", ""),
                "kind": "pagination",
                "count": len(pg.get("controls", [])),
                "controls": pg.get("controls", []),
            })

        for r in self._regions[:10]:
            if r.get("item_count", 0) >= 3:
                groups.append({
                    "group_id": f"g_repeated_data_{hash(r.get('ref', '')) & 0xFFFF:04x}",
                    "kind": "repeated_data_items",
                    "count": r.get("item_count", 0),
                    "region_ref": r.get("ref"),
                    "region_kind": r.get("kind"),
                })

        return groups

    # ─── Coverage ────────────────────────────────────────────

    def _build_coverage(
        self, all_nodes: List[Dict[str, Any]], compressed_groups: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        total = len(all_nodes)
        in_groups = sum(g.get("count", 0) for g in compressed_groups)
        region_count = len(self._regions)

        omitted = []
        if in_groups > 0:
            omitted.append({
                "type": "compressed_group",
                "count_omitted": in_groups,
                "recoverable": True,
                "recovery_method": "SnapshotQueryEngine.expand_group()",
            })

        return {
            "total_interactables": len(self._interactables),
            "shown_representatives": max(0, total - in_groups),
            "total_data_regions": region_count,
            "omitted_groups": omitted,
            "recoverable_groups": [
                {
                    "group_ref": g.get("group_id", ""),
                    "kind": g.get("kind", ""),
                    "count": g.get("count", 0),
                    "samples": (g.get("samples") or [])[:3],
                    "available_actions": g.get("available_actions") or [],
                }
                for g in compressed_groups[:8]
            ],
        }

    # ─── 工具方法 ────────────────────────────────────────────

    @staticmethod
    def _is_pagination_candidate(node: Dict[str, Any]) -> bool:
        role = str(node.get("role", "")).lower()
        tag = str(node.get("tag", "")).lower()
        return role in ("button", "link") or tag in ("a", "button")

    @staticmethod
    def _is_disabled(node: Dict[str, Any]) -> bool:
        # dp_cli snapshot record 可能没有显式 disabled/states
        text = str(node.get("name") or node.get("text") or "")
        # 检查 aria 或 class 禁用的信号
        if "disabled" in text.lower():
            return True
        return False

    def _same_ancestor(self, a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        """检查两个节点是否在同一祖先下（简化：同一 parent_ref 或 parent 相同）"""
        pa = str(a.get("parent_ref", ""))
        pb = str(b.get("parent_ref", ""))
        if pa and pb and pa == pb:
            return True
        # 检查 tree.parent_map 是否有共同祖先
        return self._find_common_ancestor(self._tree.get("parent_map", {}), a.get("ref", ""), b.get("ref", "")) is not None

    @staticmethod
    def _find_common_ancestor(
        parent_map: Dict[str, str], ref_a: str, ref_b: str, depth: int = 5
    ) -> Optional[str]:
        if not ref_a or not ref_b:
            return None
        ancestors_a: Set[str] = {ref_a}
        current = ref_a
        for _ in range(depth):
            current = parent_map.get(current, "")
            if not current:
                break
            ancestors_a.add(current)

        current = ref_b
        for _ in range(depth):
            if current in ancestors_a:
                return current
            current = parent_map.get(current, "")
            if not current:
                break
        return None
