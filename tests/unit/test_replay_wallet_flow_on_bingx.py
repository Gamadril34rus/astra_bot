from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.replay_wallet_flow_on_bingx import replay


def test_cross_venue_replay_uses_closed_context_next_open_and_funding(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "bingx"
    data_dir.mkdir()
    times = pd.date_range(
        "2026-10-02T19:30:00Z", "2026-10-03T10:00:00Z", freq="30min"
    )
    bars = pd.DataFrame({
        "open_time": (times.view("int64") // 1_000_000).astype("int64"),
        "open": [100.0] * len(times),
        "high": [101.0] * len(times),
        "low": [98.0] * len(times),
        "close": [100.0] * len(times),
        "volume": [1.0] * len(times),
        "quote_volume": [0.0] * len(times),
        "trades_count": [0] * len(times),
    })
    bars.loc[times.get_loc(pd.Timestamp("2026-10-02T19:30:00Z")), "close"] = 101.0
    bars.loc[times.get_loc(pd.Timestamp("2026-10-02T22:30:00Z")), "close"] = 99.0
    bars.loc[times.get_loc(pd.Timestamp("2026-10-02T23:30:00Z")), "close"] = 100.0
    bars.loc[times.get_loc(pd.Timestamp("2026-10-03T00:30:00Z")), "open"] = 100.0
    bars.loc[times.get_loc(pd.Timestamp("2026-10-03T09:00:00Z")), "close"] = 99.0
    bars.to_csv(data_dir / "BTCUSDT_30m.csv", index=False)

    funding_times = pd.to_datetime(
        ["2026-10-03T00:00:00Z", "2026-10-03T08:00:00Z", "2026-10-03T16:00:00Z"],
        utc=True,
    )
    pd.DataFrame({
        "funding_time": (funding_times.view("int64") // 1_000_000).astype("int64"),
        "rate": [0.0, 0.0001, 0.0001],
    }).to_csv(data_dir / "funding_BTCUSDT.csv", index=False)

    events_path = tmp_path / "events.csv"
    pd.DataFrame([{
        "horizon": "9h",
        "metric": "flow_ratio",
        "market": "BTC-PERP",
        "bucket_ts": "2026-10-03T00:00:00+00:00",
        "signal_side": "sell_pressure",
        "signal_value": -0.5,
    }]).to_csv(events_path, index=False)

    report = replay(events_path, data_dir, tmp_path / "out")

    candidate = pd.read_csv(tmp_path / "out" / "wallet_flow_bingx_candidate_events.csv")
    assert len(candidate) == 1
    row = candidate.iloc[0]
    assert row["entry_delay_minutes"] == 15
    assert row["prior_1h_return_pct"] > 0
    assert row["prior_4h_return_pct"] < 0
    assert row["funding_coverage"]
    assert abs(row["funding_return_pct"] - 0.01) < 1e-9
    assert row["net_base_pct"] > 0
    assert report["counts"]["candidate_non_overlapping_events"] == 1

    summary = json.loads(
        (tmp_path / "out" / "wallet_flow_bingx_replay_summary.json").read_text()
    )
    assert summary["status"] == "EXPLORATORY_CROSS_VENUE_EVENT_REPLAY_NOT_OOS"
    assert summary["data_window"]["data_quality"]["BTC-PERP"]["quote_volume_nonzero_rows"] == 0
