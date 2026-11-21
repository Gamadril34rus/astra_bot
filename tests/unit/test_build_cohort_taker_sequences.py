from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from scripts.research.build_cohort_taker_sequences import build_sequences


def _manifest(address: str, **filters: object) -> dict:
    return {
        "wallet_summaries": [{"address": address}],
        "filters": {
            "market_class": "perp",
            "dex": "empty string only (native Hyperliquid perps; excludes HIP-3)",
            "spot_excluded": True,
            "hip3_excluded": True,
            **filters,
        },
    }


def test_builder_uses_archive_schema_and_keeps_only_cohort_taker_sequences(
    tmp_path: Path,
) -> None:
    cohort_address = "0x" + "a" * 40
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(_manifest(cohort_address)),
        encoding="utf-8",
    )

    timestamps = pd.date_range(
        "2026-10-03T00:00:00Z", periods=10, freq="5min"
    )
    rows = []
    for index, timestamp in enumerate(timestamps):
        rows.append({
            "timestamp": timestamp,
            "market": "BTC-PERP",
            "wallet": cohort_address,
            "side": "sell",
            "crossed": True,
            "taker_side": "sell",
            "price": 100.0 - index * 0.1,
            "size": 1.0,
            "notional": 100.0,
            "block": index + 1,
            "tid": index + 1,
        })
        # The archive already expands each matched trade to a cohort wallet row.
        # A cohort maker row has crossed=False and must not enter the sequence.
        rows.append({
            "timestamp": timestamp,
            "market": "BTC-PERP",
            "wallet": cohort_address,
            "side": "buy",
            "crossed": False,
            "taker_side": "sell",
            "price": 100.0,
            "size": 1.0,
            "notional": 100.0,
            "block": index + 101,
            "tid": index + 101,
        })
    trades = tmp_path / "wallet_trades.parquet"
    pq.write_table(pa.Table.from_pylist(rows), trades)

    report = build_sequences(trades, manifest, tmp_path / "out")
    assert report["counts"]["sequences"] == 1
    assert report["by_market_side"][0]["market"] == "BTC-PERP"
    assert report["by_market_side"][0]["side"] == "sell"
    assert report["by_market_side"][0]["sequences"] == 1
    assert report["by_market_side"][0]["child_fills"] == 10

    csv_path = tmp_path / "out" / "cohort_taker_sequences.csv"
    with csv_path.open(encoding="utf-8", newline="") as handle:
        output = list(csv.DictReader(handle))
    assert len(output) == 1
    assert "address" not in output[0]
    assert "wallet" not in output[0]
    assert int(output[0]["trade_count"]) == 10
    assert float(output[0]["q_notional"]) == 1000.0
    assert float(output[0]["signed_price_move"]) > 0

    summary = json.loads(
        (tmp_path / "out" / "cohort_taker_sequence_summary.json").read_text()
    )
    assert summary["rules"]["taker_wallet_only"] is True
    assert summary["rules"]["taker_definition"] == "crossed=true for the cohort wallet fill"
    assert summary["rules"]["native_perpetual_manifest_verified"] is True
    assert summary["rules"]["future_volume_features"] is False


def test_builder_fails_closed_without_native_perpetual_manifest(tmp_path: Path) -> None:
    address = "0x" + "a" * 40
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(_manifest(address, market_class="spot", spot_excluded=False)),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="native perpetual-only"):
        build_sequences(
            tmp_path / "missing.parquet",
            manifest,
            tmp_path / "out",
        )
