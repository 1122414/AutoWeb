"""
dp_cli Snapshot Store - 全量快照落盘与回查

职责：
- 保存 full snapshot JSON 到磁盘 (output/dpcli_snapshots/{session}/)
- 管理 snapshot_id 递增
- 按 snapshot_id / snapshot_seq 回查
- 生成 dpcli_snapshot_ref 供 state 使用

三层信息架构中的 Layer 3: full snapshot JSON (权威事实源)
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import OUTPUT_DIR
from skills.logger import logger


class SnapshotStore:
    """
    快照持久化存储

    目录结构:
    output/dpcli_snapshots/{session}/
        ss_{seq}.full.json          ← 全量快照 (权威源)
        ss_{seq}.index.json         ← 可搜索索引
        ss_{seq}.compressed_index.json ← 压缩分组索引
        ss_{seq}.planner_view.json  ← Planner 视角 (lossy)
        ss_{seq}.meta.json          ← 元信息

    用法:
    store = SnapshotStore(session="autoweb")
    ref = store.save(snapshot_data)
    full = store.load_full(ref.snapshot_id)
    """

    def __init__(self, session: str = "autoweb", base_dir: Optional[str] = None):
        self.session = session
        self._base_dir = Path(base_dir) if base_dir else Path(OUTPUT_DIR) / "dpcli_snapshots"
        self._session_dir = self._base_dir / session

    @property
    def session_dir(self) -> Path:
        self._session_dir.mkdir(parents=True, exist_ok=True)
        return self._session_dir

    # ─── ID 生成 ────────────────────────────────────────────

    def _next_seq(self) -> int:
        existing = list(self.session_dir.glob("ss_*.full.json"))
        max_seq = 0
        for p in existing:
            try:
                name = p.name
                parts = name.split("_")
                if len(parts) >= 2:
                    num_part = parts[1].split(".")[0]
                    seq = int(num_part)
                    max_seq = max(max_seq, seq)
            except (IndexError, ValueError):
                pass
        return max_seq + 1

    @staticmethod
    def _make_snapshot_id(seq: int) -> str:
        return f"ss_{seq:04d}"

    @staticmethod
    def _hash(data: dict) -> str:
        return hashlib.sha256(
            json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:16]

    # ─── 保存 ────────────────────────────────────────────────

    def save_full(self, snapshot_data: Dict[str, Any]) -> Dict[str, Any]:
        """保存 full snapshot，返回 snapshot_ref"""
        seq = self._next_seq()
        snapshot_id = self._make_snapshot_id(seq)
        captured_at = datetime.now(timezone.utc).isoformat()

        page = self._extract_page(snapshot_data)
        content_hash = self._hash(snapshot_data)

        # 写全量快照
        self._write_json(f"{snapshot_id}.full.json", snapshot_data)

        ref = {
            "session": self.session,
            "snapshot_id": snapshot_id,
            "snapshot_seq": seq,
            "page_id": page.get("page_id", ""),
            "captured_at": captured_at,
            "page_url": page.get("url", ""),
            "page_title": page.get("title", ""),
            "full_snapshot_file": str(self.session_dir / f"{snapshot_id}.full.json"),
            "index_file": str(self.session_dir / f"{snapshot_id}.index.json"),
            "compressed_index_file": str(self.session_dir / f"{snapshot_id}.compressed_index.json"),
            "planner_view_file": str(self.session_dir / f"{snapshot_id}.planner_view.json"),
            "hash": content_hash,
        }
        self._write_json(f"{snapshot_id}.meta.json", ref)
        logger.info(f"   📦 [SnapshotStore] 已保存 full snapshot {snapshot_id} (session={self.session})")
        return ref

    def save_index(self, snapshot_id: str, index_data: Dict[str, Any]) -> str:
        """保存可搜索索引"""
        path = self.session_dir / f"{snapshot_id}.index.json"
        self._write_json(f"{snapshot_id}.index.json", index_data)
        logger.info(f"   📋 [SnapshotStore] 已保存 index {snapshot_id}")
        return str(path)

    def save_compressed_index(self, snapshot_id: str, compressed_data: Dict[str, Any]) -> str:
        """保存压缩分组索引"""
        path = self.session_dir / f"{snapshot_id}.compressed_index.json"
        self._write_json(f"{snapshot_id}.compressed_index.json", compressed_data)
        logger.info(f"   🗜️  [SnapshotStore] 已保存 compressed_index {snapshot_id}")
        return str(path)

    def save_planner_view(self, snapshot_id: str, view_data: Dict[str, Any]) -> str:
        """保存 planner view"""
        path = self.session_dir / f"{snapshot_id}.planner_view.json"
        self._write_json(f"{snapshot_id}.planner_view.json", view_data)
        logger.info(f"   👁️  [SnapshotStore] 已保存 planner_view {snapshot_id}")
        return str(path)

    # ─── 加载 ────────────────────────────────────────────────

    def load_full(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        return self._read_json(f"{snapshot_id}.full.json")

    def load_index(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        return self._read_json(f"{snapshot_id}.index.json")

    def load_compressed_index(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        return self._read_json(f"{snapshot_id}.compressed_index.json")

    def load_planner_view(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        return self._read_json(f"{snapshot_id}.planner_view.json")

    def load_meta(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        return self._read_json(f"{snapshot_id}.meta.json")

    def load_by_file_path(self, file_path: str) -> Optional[Dict[str, Any]]:
        """通过绝对路径加载文件"""
        p = Path(file_path)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    # ─── 列表 ────────────────────────────────────────────────

    def list_snapshots(self) -> List[Dict[str, Any]]:
        """列出所有快照的 meta 信息"""
        metas = []
        for p in sorted(self.session_dir.glob("ss_*.meta.json")):
            meta = self._read_json(p.name)
            if meta:
                metas.append(meta)
        return metas

    def latest_snapshot_id(self) -> Optional[str]:
        """获取最新快照 ID"""
        metas = self.list_snapshots()
        if not metas:
            return None
        return max(metas, key=lambda m: m.get("snapshot_seq", 0)).get("snapshot_id")

    # ─── 内部工具 ────────────────────────────────────────────

    def _write_json(self, filename: str, data: Dict[str, Any]) -> None:
        path = self.session_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    def _read_json(self, filename: str) -> Optional[Dict[str, Any]]:
        path = self.session_dir / filename
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"   ⚠️ [SnapshotStore] 读取JSON失败: {path} - {e}")
            return None

    @staticmethod
    def _extract_page(snapshot: Dict[str, Any]) -> Dict[str, Any]:
        data = snapshot.get("data") if isinstance(snapshot, dict) else {}
        page = data.get("page") if isinstance(data, dict) else {}
        identity = data.get("page_identity") if isinstance(data, dict) else {}
        if not isinstance(page, dict):
            page = {}
        if not isinstance(identity, dict):
            identity = {}
        return {
            "url": page.get("url", ""),
            "title": page.get("title", ""),
            "page_id": identity.get("page_id", ""),
            "domain": identity.get("domain", ""),
        }
