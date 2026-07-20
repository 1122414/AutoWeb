from __future__ import annotations

from types import SimpleNamespace

from skills.task_resume import parse_resume_thread_id, snapshot_has_checkpoint


def test_parse_resume_thread_id_accepts_english_and_chinese_commands():
    thread_id = "550e8400-e29b-41d4-a716-446655440000"

    assert parse_resume_thread_id(f"resume {thread_id}") == thread_id
    assert parse_resume_thread_id(f"恢复 {thread_id}") == thread_id


def test_parse_resume_thread_id_rejects_missing_or_unsafe_ids():
    assert parse_resume_thread_id("resume") is None
    assert parse_resume_thread_id("resume ../../secrets") is None
    assert parse_resume_thread_id("start abcdef") is None


def test_snapshot_checkpoint_detection_covers_values_and_next_nodes():
    assert snapshot_has_checkpoint(SimpleNamespace(values={"loop_count": 1}, next=()))
    assert snapshot_has_checkpoint(SimpleNamespace(values={}, next=("Executor",)))
    assert not snapshot_has_checkpoint(SimpleNamespace(values={}, next=()))
