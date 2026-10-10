"""Tests for Zeus state rotation (GitHub 100 MB per-blob limit)."""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rotate_zeus_states as rot


def _write(path: Path, lines: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f'{{"i": {i}}}\n' for i in range(lines)), encoding="utf-8")


def test_rotate_over_threshold_moves_old_lines_to_gz(tmp_path):
    """Выше порога: старые строки уходят в gz-архив, хвост остаётся в файле."""
    src = tmp_path / "models" / "zeus_trade_journal.jsonl"
    _write(src, 100)
    archive_dir = tmp_path / "models" / "archive"

    res = rot.rotate_file(src, max_bytes=400, keep_lines=20, archive_dir=archive_dir)

    assert res["rotated"] is True
    assert res["skipped"] == ""
    assert res["archived_lines"] == 80
    assert res["kept_lines"] == 20
    assert res["size_after"] < 400

    archives = list(archive_dir.glob("*.jsonl.gz"))
    assert len(archives) == 1
    with gzip.open(archives[0], "rt", encoding="utf-8") as fh:
        archived = fh.read().splitlines()
    assert len(archived) == 80
    assert archived[0] == '{"i": 0}'
    assert archived[-1] == '{"i": 79}'

    kept = src.read_text(encoding="utf-8").splitlines()
    assert len(kept) == 20
    assert kept[0] == '{"i": 80}'
    assert kept[-1] == '{"i": 99}'


def test_rotate_under_threshold_keeps_file_intact(tmp_path):
    """Ниже порога: файл не тронут, каталог архива даже не создаётся."""
    src = tmp_path / "models" / "zeus_paper_trades.jsonl"
    _write(src, 30)
    before = src.read_bytes()
    archive_dir = tmp_path / "models" / "archive"

    res = rot.rotate_file(src, max_bytes=400, keep_lines=20, archive_dir=archive_dir)

    assert res["rotated"] is False
    assert res["skipped"] == "under_threshold"
    assert res["archived_lines"] == 0
    assert src.read_bytes() == before
    assert not archive_dir.exists()
