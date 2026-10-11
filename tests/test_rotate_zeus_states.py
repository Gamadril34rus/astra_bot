"""Tests for Zeus state rotation (GitHub 100 MB per-blob limit)."""

from __future__ import annotations

import gzip
import json
import sys
import time
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


def test_line_trigger_rotates_file_over_line_limit_below_size(tmp_path):
    """Строчный триггер: 25k строк при лимите LIVE_JSONL_LIMITS=24k и весе < 20 МБ.

    Размерный порог не срабатывает, строчный — срабатывает: хвост 20k, архив создан.
    Проверяем через rotate_all, т.е. тем же путём, что и в тике (лимит из
    LIVE_JSONL_LIMITS, а не из теста).
    """
    assert rot.LIVE_JSONL_LIMITS["zeus_wedge_1h_signals.jsonl"] == 24_000
    src = tmp_path / "models" / "zeus_wedge_1h_signals.jsonl"
    _write(src, 25_000)
    assert src.stat().st_size < rot.MAX_BYTES
    archive_dir = tmp_path / "models" / "archive"

    results = rot.rotate_all(
        ("models/zeus_wedge_1h_signals.jsonl",),
        root=tmp_path,
        archive_dir=archive_dir,
    )

    res = results[0]
    assert res["rotated"] is True
    assert res["archived_lines"] == 5_000
    assert res["kept_lines"] == 20_000
    archives = list(archive_dir.glob("*.jsonl.gz"))
    assert len(archives) == 1
    kept = src.read_text(encoding="utf-8").splitlines()
    assert len(kept) == 20_000
    assert kept[0] == '{"i": 5000}'
    assert kept[-1] == '{"i": 24999}'


def test_outcomes_keep_full_cap_and_do_not_evict_ids_in_observations(tmp_path):
    """24k outcomes >= 24k observations: generic 20k tail would re-enrich 4k ids."""
    from astra_bot.ml.no_trade_observations import NoTradeObservationLog

    rel = "models/zeus_no_trade_outcomes.jsonl"
    limit = rot.LIVE_JSONL_LIMITS["zeus_no_trade_outcomes.jsonl"]
    assert rel in rot.STATE_FILES
    assert limit == 24_000
    assert limit >= rot.LIVE_JSONL_LIMITS["zeus_no_trade_observations.jsonl"]
    assert rot.LIVE_JSONL_LIMITS["no_trade_outcomes.jsonl"] >= rot.LIVE_JSONL_LIMITS["no_trade_observations.jsonl"]
    src = tmp_path / rel
    src.parent.mkdir()
    base = int(time.time() * 1000) - 25_000
    rows = [
        {"id": f"obs-{i}", "bar_time": base + i, "symbol": "BTC-USDT", "horizons": {"1": {}}}
        for i in range(25_000)
    ]
    src.write_text("".join(json.dumps(row) + "\n" for row in rows))
    observations = src.with_name("zeus_no_trade_observations.jsonl")
    # No backfill result: this checks retention alignment itself, not the extra
    # protection pending() obtains from resolved observation rows.
    observations.write_text("".join(json.dumps({
        "id": row["id"], "bar_time": row["bar_time"], "symbol": row["symbol"], "result": None,
    }) + "\n" for row in rows[-limit:]))
    assert src.stat().st_size < rot.MAX_BYTES
    result = rot.rotate_all((rel,), root=tmp_path)[0]
    assert result["rotated"] is True
    assert result["archived_lines"] == 1_000
    assert result["kept_lines"] == limit
    kept = src.read_text().splitlines()
    assert json.loads(kept[0])["id"] == "obs-1000"
    assert json.loads(kept[-1])["id"] == "obs-24999"
    log = NoTradeObservationLog(observations_path=observations, outcomes_path=src)
    assert log.pending(limit=25_000) == []
    assert log.enrich({}) == []
    assert src.read_text().splitlines() == kept
