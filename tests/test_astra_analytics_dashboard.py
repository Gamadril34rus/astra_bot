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
    assert data["no_trade_analysis"]["available"] is False
    assert data["no_trade_analysis"]["observations"] == 0
    assert data["no_trade_analysis"]["with_outcome"] == 0


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


def test_no_trade_observations_join_outcomes_and_aggregate_metrics(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "models" / "no_trade_observations.jsonl", [
        {
            "id": "nt-1", "symbol": "BTC-USDT", "bar_time": 1790000000000,
            "timestamp": 1790000000000, "reason_code": "NO_VALID_SETUP",
            "rejection_stage": "strategy", "market_regime": "RANGE",
            "candidate": {"direction": "long"},
        },
        {
            "id": "nt-2", "symbol": "ETH-USDT", "bar_time": 1790000060000,
            "timestamp": 1790000060000, "reason_code": "PORTFOLIO_CAP",
            "rejection_stage": "portfolio", "market_regime": "TREND",
            "result": {
                "1": {"future_return": -0.01, "max_up": 0.01, "max_down": -0.02}
            },
            "hypothetic_r": {"r": -1.0, "exit_kind": "stop", "bars": 2},
        },
        {
            "id": "nt-3", "symbol": "SOL-USDT", "bar_time": 1790000120000,
            "timestamp": 1790000120000, "reason_code": "NO_VALID_SETUP",
            "rejection_stage": "strategy", "market_regime": "RANGE",
        },
    ])
    write_jsonl(tmp_path / "models" / "no_trade_outcomes.jsonl", [
        {
            "id": "nt-1", "bar_time": 1790000000000, "symbol": "BTC-USDT",
            "horizons": {
                "1": {"future_return": 0.02, "max_up": 0.03, "max_down": -0.01},
                "3": {"future_return": 0.04, "max_up": 0.05, "max_down": -0.015},
            },
            "hypothetic_r": {"r": 2.0, "exit_kind": "take", "bars": 2},
        }
    ])

    no_trade = build_payload(tmp_path)["no_trade_analysis"]
    assert no_trade["available"] is True
    assert no_trade["outcomes_available"] is True
    assert no_trade["observations"] == 3
    assert no_trade["with_outcome"] == 2
    assert no_trade["pending_outcome"] == 1
    assert no_trade["reason_codes"] == {
        "NO_VALID_SETUP": 2, "PORTFOLIO_CAP": 1
    }
    assert no_trade["rejection_stages"] == {"strategy": 2, "portfolio": 1}
    assert no_trade["market_regimes"] == {"RANGE": 2, "TREND": 1}
    assert no_trade["horizon_metrics"]["1"] == {
        "n": 2, "avg_future_return_pct": 0.5,
        "avg_max_up_pct": 2.0, "avg_max_down_pct": -1.5,
    }
    assert no_trade["horizon_metrics"]["3"]["n"] == 1
    assert no_trade["hypothetical_r"]["n"] == 2
    assert no_trade["hypothetical_r"]["complete_n"] == 2
    assert no_trade["hypothetical_r"]["avg_r"] == 0.5
    assert no_trade["hypothetical_r"]["positive_r"] == 1
    assert no_trade["hypothetical_r"]["non_positive_r"] == 1
    assert no_trade["hypothetical_r"]["exit_kinds"] == {"stop": 1, "take": 1}


def test_no_trade_analysis_uses_legacy_outcomes_only_without_jsonl(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "models" / "no_trade_observations.jsonl", [
        {"id": "legacy-1", "reason_code": "NO_VALID_SETUP", "market_regime": "RANGE"}
    ])
    (tmp_path / "models").mkdir(exist_ok=True)
    (tmp_path / "models" / "no_trade_outcomes.json").write_text(
        json.dumps({"outcomes": {
            "legacy-1": {
                "bar_time": 1790000000000,
                "horizons": {"1": {"future_return": 0.01, "max_up": 0.02, "max_down": -0.005}},
            }
        }}),
        encoding="utf-8",
    )
    analysis = build_payload(tmp_path)["no_trade_analysis"]
    assert analysis["outcomes_available"] is True
    assert analysis["with_outcome"] == 1
    assert analysis["horizon_metrics"]["1"]["n"] == 1
