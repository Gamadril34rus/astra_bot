from __future__ import annotations

import json

import pandas as pd
from docs.research.sar_repro.apply_sar_funding import apply_funding


def test_apply_funding_uses_correct_long_short_sign_and_boundaries(tmp_path):
    funding_dir = tmp_path / "funding"
    funding_dir.mkdir()
    funding_times = [
        "2026-10-01T00:00:00Z",
        "2026-10-01T08:00:00Z",
        "2026-10-01T16:00:00Z",
        "2026-10-02T00:00:00Z",
    ]
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        pd.DataFrame({
            "funding_time": [
                int(pd.Timestamp(value).timestamp() * 1000)
                for value in funding_times
            ],
            "rate": [0.0001, 0.0002, 0.0001, 0.0001],
        }).to_csv(funding_dir / f"funding_{symbol}.csv", index=False)

    trades = pd.DataFrame([
        {
            "symbol": "BTC-USDT", "side": "long",
            "entry_ts": int(pd.Timestamp("2026-10-01T00:00:00Z").timestamp() * 1000),
            "exit_ts": int(pd.Timestamp("2026-10-01T08:00:00Z").timestamp() * 1000),
            "net_pnl_pct": -0.1,
        },
        {
            "symbol": "ETH-USDT", "side": "short",
            "entry_ts": int(pd.Timestamp("2026-10-01T00:00:00Z").timestamp() * 1000),
            "exit_ts": int(pd.Timestamp("2026-10-01T08:00:00Z").timestamp() * 1000),
            "net_pnl_pct": -0.1,
        },
        {
            "symbol": "SOL-USDT", "side": "long",
            "entry_ts": int(pd.Timestamp("2026-10-01T00:00:00Z").timestamp() * 1000),
            "exit_ts": int(pd.Timestamp("2026-10-01T04:00:00Z").timestamp() * 1000),
            "net_pnl_pct": -0.1,
        },
    ])
    trades_path = tmp_path / "trades.csv"
    trades.to_csv(trades_path, index=False)

    report = apply_funding(trades_path, funding_dir, tmp_path / "out")
    adjusted = pd.read_csv(
        tmp_path / "out" / "sar_15m_funding_adjusted_trades.csv"
    )
    assert len(adjusted) == 3
    assert adjusted.funding_pnl_pct.iloc[0] == -0.0002
    assert adjusted.funding_pnl_pct.iloc[1] == 0.0002
    assert adjusted.funding_pnl_pct.iloc[2] == 0.0
    assert report["funding_coverage"]["covered_trades"] == 3
    saved = json.loads(
        (tmp_path / "out" / "sar_15m_funding_adjusted_summary.json").read_text()
    )
    assert saved["funding_coverage"]["coverage_pct"] == 100.0


def test_apply_funding_excludes_trades_without_complete_archive_window(tmp_path):
    funding_dir = tmp_path / "funding"
    funding_dir.mkdir()
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        pd.DataFrame({
            "funding_time": [
                int(pd.Timestamp("2026-10-01T00:00:00Z").timestamp() * 1000),
                int(pd.Timestamp("2026-10-01T08:00:00Z").timestamp() * 1000),
                int(pd.Timestamp("2026-10-02T00:00:00Z").timestamp() * 1000),
                int(pd.Timestamp("2026-10-02T08:00:00Z").timestamp() * 1000),
                int(pd.Timestamp("2026-10-02T16:00:00Z").timestamp() * 1000),
            ],
            "rate": [0.0001, 0.0001, 0.0001, 0.0001, 0.0001],
        }).to_csv(funding_dir / f"funding_{symbol}.csv", index=False)
    trades = pd.DataFrame([{
        "symbol": "BTC-USDT", "side": "long",
        "entry_ts": int(pd.Timestamp("2026-10-01T01:00:00Z").timestamp() * 1000),
        "exit_ts": int(pd.Timestamp("2026-10-01T09:00:00Z").timestamp() * 1000),
        "net_pnl_pct": -0.1,
    }])
    trades_path = tmp_path / "trades.csv"
    trades.to_csv(trades_path, index=False)
    report = apply_funding(trades_path, funding_dir, tmp_path / "out")
    assert report["funding_coverage"]["covered_trades"] == 0
    assert report["funding_coverage"]["excluded_by_reason"]["funding_gap"] == 1
