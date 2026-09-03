"""State rotation / size gate (TZ §28/§29)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from astra_bot.core.state_rotation import (
    LIVE_JSONL_LIMITS,
    jsonl_integrity,
    rotate_all,
    rotate_jsonl,
    size_gate,
)


def _write_lines(path: Path, n: int, prefix: str = "row") -> None:
    with path.open("w", encoding="utf-8") as f:
        for i in range(n):
            f.write(json.dumps({"i": i, "tag": prefix}) + "\n")


class TestRotateJsonl:
    def test_below_limit_untouched(self, tmp_path):
        p = tmp_path / "obs.jsonl"
        _write_lines(p, 10)
        assert rotate_jsonl(p, 20) == 0
        assert len(p.read_text().strip().splitlines()) == 10

    def test_above_limit_keeps_tail_archives_head(self, tmp_path):
        p = tmp_path / "obs.jsonl"
        _write_lines(p, 30)
        moved = rotate_jsonl(p, 10)
        assert moved == 20
        lines = p.read_text().strip().splitlines()
        assert len(lines) == 10
        # Остался ХВОСТ (новые строки).
        assert json.loads(lines[0])["i"] == 20
        assert json.loads(lines[-1])["i"] == 29
        # Голова — в append-only архиве.
        archive = tmp_path / "obs.archive.jsonl"
        assert archive.exists()
        archived = archive.read_text().strip().splitlines()
        assert len(archived) == 20
        assert json.loads(archived[0])["i"] == 0
        assert json.loads(archived[-1])["i"] == 19

    def test_multiple_rotations_append_to_archive(self, tmp_path):
        p = tmp_path / "obs.jsonl"
        _write_lines(p, 25)
        rotate_jsonl(p, 10)
        _write_lines(p, 25)  # «новая сессия»
        rotate_jsonl(p, 10)
        archived = (tmp_path / "obs.archive.jsonl").read_text().strip().splitlines()
        assert len(archived) == 30  # 15 + 15, архив только растёт
        assert len(p.read_text().strip().splitlines()) == 10

    def test_no_empty_lines_introduced(self, tmp_path):
        p = tmp_path / "obs.jsonl"
        _write_lines(p, 12)
        rotate_jsonl(p, 5)
        assert jsonl_integrity(p) == []
        assert jsonl_integrity(tmp_path / "obs.archive.jsonl") == []

    def test_missing_file(self, tmp_path):
        assert rotate_jsonl(tmp_path / "nope.jsonl", 10) == 0


class TestRotateAllAndGate:
    def test_rotate_all_touches_live_files(self, tmp_path):
        big = tmp_path / "no_trade_observations.jsonl"
        _write_lines(big, 6000)
        small = tmp_path / "decision_log.jsonl"
        _write_lines(small, 10)
        moved = rotate_all(tmp_path)
        assert moved == {"no_trade_observations.jsonl": 1000}
        assert len(big.read_text().strip().splitlines()) == 5000
        assert len(small.read_text().strip().splitlines()) == 10

    def test_size_gate_passes_after_rotation(self, tmp_path):
        big = tmp_path / "no_trade_observations.jsonl"
        _write_lines(big, 6000)
        rotate_all(tmp_path)
        assert size_gate(tmp_path) == []

    def test_size_gate_detects_growth(self, tmp_path):
        big = tmp_path / "no_trade_observations.jsonl"
        _write_lines(big, 6000)
        rotate_all(tmp_path)
        # Сессия добавила чуть-чуть — ок...
        with big.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"i": 99999}) + "\n")
        assert size_gate(tmp_path) == []
        # ...но не «бесконечно».
        with big.open("a", encoding="utf-8") as f:
            for i in range(600):
                f.write(json.dumps({"i": i}) + "\n")
        v = size_gate(tmp_path)
        assert len(v) == 1
        assert "no_trade_observations.jsonl" in v[0]

    def test_limits_defined_for_all_live_files(self):
        assert len(LIVE_JSONL_LIMITS) >= 5
        for _rel, limit in LIVE_JSONL_LIMITS.items():
            assert limit > 0


class TestCheckStateSizeScript:
    def test_script_fails_on_growth(self, tmp_path):
        big = tmp_path / "no_trade_observations.jsonl"
        _write_lines(big, 20_000)
        root = Path(__file__).resolve().parents[2]
        res = subprocess.run(
            [sys.executable, str(root / "scripts" / "check_state_size.py"),
             "--root", str(tmp_path)],
            capture_output=True, text=True, timeout=120,
        )
        assert res.returncode == 1
        assert "VIOLATION" in res.stderr

    def test_script_passes_clean(self, tmp_path):
        p = tmp_path / "no_trade_observations.jsonl"
        _write_lines(p, 50)
        root = Path(__file__).resolve().parents[2]
        res = subprocess.run(
            [sys.executable, str(root / "scripts" / "check_state_size.py"),
             "--root", str(tmp_path)],
            capture_output=True, text=True, timeout=120,
        )
        assert res.returncode == 0
        assert "OK" in res.stdout
