from __future__ import annotations

import json
from pathlib import Path

from scripts.astra_analytics_dashboard import build_payload, generate


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_separates_contours_and_coalesces_paper_legs(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "models" / "zeus_paper_trades.jsonl", [
        {"id": "p1", "strategy": "channel", "pnl": 2, "fees": .1,
         "r_multiple": .5, "opened_at": 1790000000000, "closed_at": 1790000060000},
        {"id": "p1", "strategy": "channel", "pnl": 1, "fees": .1,
         "r_multiple": .3, "opened_at": 1790000000000, "closed_at": 1790000120000},
    ])
    write_jsonl(tmp_path / "models" / "zeus_live_trades.jsonl", [
        {"event": "exit", "pnl": 5, "ts": 1790100000, "env": "VST",
         "strategy": "zeus_channel_boundary"},
        {"event": "exit", "pnl": -2, "ts": 1790200000, "env": "REAL",
         "strategy": "zeus_wedge"},
        {"event": "exit", "pnl": 3, "ts": 1790300000,
         "strategy": "zeus_channel_boundary"},
    ])
    data = build_payload(tmp_path)
    assert {r["mode"] for r in data["strategy_metrics"]} == {
        "PAPER", "DEMO / VST", "LIVE / REAL", "LIVE / UNKNOWN"
    }
    assert data["data_quality"]["paper_trades"] == 1
    assert data["timelines"]["paper"][0]["cumulative_pnl"] == 3
    assert data["data_quality"]["environment_classification"] == {
        "VST": 1, "REAL": 1, "UNKNOWN": 1
    }


def test_missing_sources_remain_missing_not_zero(tmp_path: Path) -> None:
    (tmp_path / "models").mkdir()
    data = build_payload(tmp_path)
    assert data["volume_zone_shadow"]["available"] is False
    assert data["signal_funnel"]["pending_candidates"] is None
    assert data["signal_funnel"]["scan_attempts"] is None
    assert data["system_health"]["journal_errors"] is None


def test_generates_html_and_machine_readable_json(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "models" / "zeus_paper_trades.jsonl", [
        {"id": "p1", "pnl": 1, "fees": 0, "r_multiple": .5,
         "opened_at": 1790000000000, "closed_at": 1790000060000}
    ])
    target = generate(tmp_path)
    assert (target / "index.html").is_file()
    assert (target / "dashboard.json").is_file()
    report = json.loads((target / "dashboard.json").read_text(encoding="utf-8"))
    assert report["data_quality"]["paper_trades"] == 1
    assert "<!doctype html>" in (target / "index.html").read_text(encoding="utf-8")
