from __future__ import annotations

import csv

from skills.toolbox import download_file, save_to_csv


class _DownloadResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    @staticmethod
    def raise_for_status():
        return None

    @staticmethod
    def iter_bytes():
        yield b"hello"
        yield b"-world"


def test_download_file_supports_filename_without_parent(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "skills.toolbox.httpx.stream",
        lambda *_args, **_kwargs: _DownloadResponse(),
    )

    assert download_file("https://example.test/data", "download.bin")
    assert (tmp_path / "download.bin").read_bytes() == b"hello-world"


def test_save_to_csv_uses_exact_path_and_union_of_fields(tmp_path):
    output = tmp_path / "nested" / "result.csv"

    assert save_to_csv(
        [{"title": "A"}, {"title": "B", "url": "/b"}],
        str(output),
    )

    assert output.exists()
    with output.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows == [
        {"title": "A", "url": ""},
        {"title": "B", "url": "/b"},
    ]
