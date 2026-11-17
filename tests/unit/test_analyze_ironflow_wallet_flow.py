import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from scripts.analyze_ironflow_wallet_flow import HORIZONS, _sql_path, analyze


def test_forward_horizons_match_at_hold_time_cap() -> None:
    assert HORIZONS == {
        "15m": "close_15m",
        "30m": "close_30m",
        "1h": "close_1h",
        "4h": "close_4h",
        "9h": "close_9h",
    }


def test_sql_path_escapes_single_quotes(tmp_path: Path) -> None:
    path = tmp_path / "wallet's trades.parquet"
    assert _sql_path(path).endswith("wallet''s trades.parquet")


def test_analyze_builds_wallet_flow_and_exact_forward_returns(tmp_path: Path) -> None:
    start = datetime(2026, 10, 3, 0, 0, tzinfo=timezone.utc)
    wallet_trades = pa.table({
        "wallet": ["0xa", "0xb", "0xc"],
        "timestamp": [start, start, start],
        "market": ["BTC-PERP"] * 3,
        "price": [100.0, 100.0, 100.0],
        "size": [1.0, 2.0, 3.0],
        "notional": [100.0, 200.0, 300.0],
        "taker_side": ["buy"] * 3,
        "side": ["buy"] * 3,
        "crossed": [True] * 3,
        "liquidation": [False] * 3,
        "block": [1, 1, 1],
        "tid": [1, 2, 3],
    })
    wallet_path = tmp_path / "wallet_trades.parquet"
    pq.write_table(wallet_trades, wallet_path)

    offsets = [0, 15, 30, 60, 240, 540]
    closes = [100.0, 101.0, 102.0, 104.0, 108.0, 110.0]
    market_bars = pa.table({
        "market": ["BTC-PERP"] * len(offsets),
        "coin": ["BTC"] * len(offsets),
        "bucket_ts": [start + timedelta(minutes=minute) for minute in offsets],
        "open": closes,
        "high": closes,
        "low": closes,
        "close": closes,
        "base_volume": [1.0] * len(offsets),
        "quote_volume": [100.0] * len(offsets),
        "trade_count": [1] * len(offsets),
        "taker_buy_notional": [100.0] * len(offsets),
        "taker_sell_notional": [0.0] * len(offsets),
    })
    bars_path = tmp_path / "market_15m.parquet"
    pq.write_table(market_bars, bars_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({
        "source": {
            "dataset_id": "test",
            "start_date_utc": "2026-10-03",
            "end_date_utc": "2026-10-03",
            "attribution": "test",
            "license": "test",
        },
        "output": {
            "wallet_trades_sha256": "test-wallet",
            "market_15m_sha256": "test-bars",
        },
    }), encoding="utf-8")

    report = analyze(wallet_path, bars_path, manifest_path, tmp_path / "out")

    assert report["counts"]["wallet_flow_15m_rows"] == 3
    assert report["counts"]["cohort_flow_15m_rows"] == 1
    assert report["counts"]["cohort_flow_forward_return_rows"] == 1
    assert len(report["event_study"]) == 5
    assert report["event_study"][0]["horizon"] == "15m"
