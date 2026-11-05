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

def test_no_trade_observations_join_forward_outcomes_and_aggregate_by_reason(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "models" / "zeus_no_trade_observations.jsonl", [
        {
            "id": "obs-1", "symbol": "BTC-USDT", "bar_time": 1_790_000_000_000,
            "timestamp": 1_790_000_000_100, "market_regime": "LOW_VOLATILITY",
            "reason_code": "LOW_EV", "rejection_stage": "meta_strategy",
            "candidate": {"strategy": "zeus_channel_boundary_4h", "direction": "long"},
            "result": None,
        },
        {
            "id": "obs-2", "symbol": "ETH-USDT", "bar_time": 1_790_000_300_000,
            "timestamp": 1_790_000_300_100, "market_regime": "WEAK_BULL_TREND",
            "reason_code": "NO_VALID_SETUP", "rejection_stage": "structure",
            "candidate": None, "result": None,
        },
    ])
    write_jsonl(tmp_path / "models" / "zeus_no_trade_outcomes.jsonl", [
        {
            "id": "obs-1", "bar_time": 1_790_000_000_000, "symbol": "BTC-USDT",
            "reason_code": "LOW_EV", "market_regime": "LOW_VOLATILITY",
            "horizons": {
                "1": {"future_return": .01, "max_up": .02, "max_down": -.005},
                "3": {"future_return": .015, "max_up": .025, "max_down": -.006},
            },
            "hypothetic_r": {"r": 1.25, "exit_kind": "target", "bars": 3},
        }
    ])

    data = build_payload(tmp_path)
    no_trade = data["no_trade_analytics"]

    assert no_trade["observations_count"] == 2
    assert no_trade["outcome_rows"] == 1
    assert no_trade["candidate_count"] == 1
    assert no_trade["observations_with_outcome"] == 1
    assert no_trade["outcome_coverage_pct"] == 50.0
    assert no_trade["reason_codes"] == {"LOW_EV": 1, "NO_VALID_SETUP": 1}
    assert no_trade["rejection_stages"] == {"meta_strategy": 1, "structure": 1}
    assert no_trade["candidate_strategies"] == {"zeus_channel_boundary_4h": 1}
    assert no_trade["candidate_directions"] == {"long": 1}
    assert no_trade["horizons"]["1"]["samples"] == 1
    assert no_trade["horizons"]["1"]["avg_future_return_pct"] == 1.0
    assert no_trade["horizons"]["1"]["avg_mfe_pct"] == 2.0
    assert no_trade["horizons"]["1"]["avg_mae_pct"] == -0.5
    assert no_trade["hypothetic_r"]["samples"] == 1
    assert no_trade["hypothetic_r"]["avg_r"] == 1.25
    assert no_trade["hypothetic_r"]["positive_rate_pct"] == 100.0


def test_no_trade_missing_and_empty_sources_are_distinguished(tmp_path: Path) -> None:
    (tmp_path / "models").mkdir()
    missing = build_payload(tmp_path)["no_trade_analytics"]
    assert missing["observations_count"] is None
    assert missing["outcome_rows"] is None
    assert missing["outcome_coverage_pct"] is None

    write_jsonl(tmp_path / "models" / "zeus_no_trade_observations.jsonl", [])
    write_jsonl(tmp_path / "models" / "zeus_no_trade_outcomes.jsonl", [])
    empty = build_payload(tmp_path)["no_trade_analytics"]
    assert empty["observations_count"] == 0
    assert empty["outcome_rows"] == 0
    assert empty["candidate_count"] == 0
    assert empty["outcome_coverage_pct"] is None


def test_dashboard_renders_no_trade_reason_and_horizon_sections(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "models" / "zeus_no_trade_observations.jsonl", [
        {"id": "obs-1", "symbol": "BTC-USDT", "reason_code": "LOW_EV",
         "rejection_stage": "meta_strategy", "market_regime": "LOW_VOLATILITY",
         "candidate": {"strategy": "channel", "direction": "long"}, "result": None}
    ])
    write_jsonl(tmp_path / "models" / "zeus_no_trade_outcomes.jsonl", [
        {"id": "obs-1", "horizons": {"1": {"future_return": .01,
         "max_up": .02, "max_down": -.005}}, "hypothetic_r": {"r": 1.0}}
    ])
    from scripts.astra_analytics_dashboard import render_html

    payload = build_payload(tmp_path)
    page = render_html(payload)
    assert "NO_TRADE · reason codes" in page
    assert "NO_TRADE · forward outcome quality" in page
    assert "LOW_EV" in page
    assert "Horizon (bars)" in page
