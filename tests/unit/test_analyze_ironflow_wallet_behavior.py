from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from scripts.analyze_ironflow_wallet_behavior import analyze


def test_analyze_writes_wallet_flow_and_synchrony_evidence(tmp_path: Path) -> None:
    source = tmp_path / "wallet_trades.parquet"
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "out"

    con = duckdb.connect()
    con.execute("""
        CREATE TABLE fills (
            timestamp TIMESTAMP,
            market VARCHAR,
            price DOUBLE,
            size DOUBLE,
            notional DOUBLE,
            side VARCHAR,
            crossed BOOLEAN,
            wallet VARCHAR,
            liquidation BOOLEAN,
            tid BIGINT
        )
    """)
    con.executemany(
        "INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (datetime(2026, 10, 3, 0, 0, tzinfo=timezone.utc), "BTC-PERP", 100, 1, 100, "buy", True, "0xa", False, 1),
            (datetime(2026, 10, 3, 0, 1, tzinfo=timezone.utc), "BTC-PERP", 101, 1, 101, "buy", False, "0xa", False, 2),
            (datetime(2026, 10, 3, 0, 2, tzinfo=timezone.utc), "BTC-PERP", 102, 1, 102, "sell", True, "0xb", False, 3),
            (datetime(2026, 10, 3, 0, 3, tzinfo=timezone.utc), "BTC-PERP", 103, 1, 103, "buy", True, "0xb", False, 4),
        ],
    )
    con.execute(f"COPY fills TO '{source.as_posix()}' (FORMAT PARQUET)")
    con.close()
    manifest.write_text(json.dumps({"source": {"start_date_utc": "2026-10-03", "end_date_utc": "2026-10-03"}}))

    report = analyze(source, manifest, output)

    assert report["input"]["rows"] == 4
    assert report["input"]["wallets"] == 2
    assert report["input"]["markets"] == 1
    assert (output / "wallet_market_behavior.csv").is_file()
    assert (output / "wallet_market_flow_5m.parquet").is_file()
    assert (output / "market_cohort_flow_5m.csv").is_file()
    assert (output / "wallet_synchrony_pairs.csv").is_file()
    assert (output / "wallet_behavior_analysis_manifest.json").is_file()
