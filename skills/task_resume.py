"""Small, dependency-free helpers for resuming LangGraph task threads."""

from __future__ import annotations

import re
from typing import Any, Optional


_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{3,127}$")


def parse_resume_thread_id(command: str) -> Optional[str]:
    """Return a validated thread id from ``resume/恢复 <id>`` commands."""
    text = str(command or "").strip()
    match = re.fullmatch(r"(?:resume|恢复)\s+(.+)", text, flags=re.IGNORECASE)
    if not match:
        return None
    thread_id = match.group(1).strip()
    return thread_id if _THREAD_ID_RE.fullmatch(thread_id) else None


def snapshot_has_checkpoint(snapshot: Any) -> bool:
    """Whether a LangGraph state snapshot contains resumable state."""
    if snapshot is None:
        return False
    values = getattr(snapshot, "values", None) or {}
    next_nodes = getattr(snapshot, "next", None) or ()
    return bool(values or next_nodes)
