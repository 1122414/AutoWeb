from __future__ import annotations

from skills.crawl_data_quality import annotate_result_provenance


def test_annotate_result_provenance_marks_each_extracted_record():
    result = annotate_result_provenance(
        {
            "current_url": "https://example.test/products",
            "dpcli_snapshot_ref": {"snapshot_id": "snap-7"},
        },
        {"skill": "extract", "params": {"schema": ["title", "url"]}},
        {
            "ok": True,
            "action": "extract",
            "data": {
                "page": {"url": "https://example.test/products?page=2"},
                "items": [{"title": "A", "url": "https://example.test/a"}],
            },
        },
        captured_at="2026-07-29T00:00:00+00:00",
    )

    item = result["data"]["items"][0]
    assert item["_provenance"] == {
        "source_url": "https://example.test/products?page=2",
        "snapshot_id": "snap-7",
        "action": "extract",
        "captured_at": "2026-07-29T00:00:00+00:00",
    }
    assert item["_quality"]["valid_fields"] == {"title": True, "url": True}
    assert item["_quality"]["issues"] == []
