from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from scripts.research.build_cohort_taker_sequences import build_sequences


def test_builder_keeps_only_cohort_taker_sequences_and_exports_no_addresses(
    tmp_path: Path,
) -> None:
    cohort_address = "0x" + "a" * 40
    outsider = "0x" + "b" * 40
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"wallet_summaries": [{"address": cohort_address}]}),
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
            "buyer": outsider,
            "seller": cohort_address,
            "taker_side": "sell",
            "price": 100.0 - index * 0.1,
            "size": 1.0,
            "notional": 100.0,
            "block": index + 1,
            "tid": index + 1,
            "market_class": "perp",
        })
        # Cohort maker fill: should not be mistaken for cohort taker activity.
        rows.append({
            "timestamp": timestamp,
            "market": "BTC-PERP",
            "buyer": cohort_address,
            "seller": outsider,
            "taker_side": "sell",
            "price": 100.0,
            "size": 1.0,
            "notional": 100.0,
            "block": index + 101,
            "tid": index + 101,
            "market_class": "perp",
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
    assert int(output[0]["trade_count"]) == 10
    assert float(output[0]["q_notional"]) == 1000.0
    assert float(output[0]["signed_price_move"]) > 0

    summary = json.loads(
        (tmp_path / "out" / "cohort_taker_sequence_summary.json").read_text()
    )
    assert summary["rules"]["taker_wallet_only"] is True
    assert summary["rules"]["future_volume_features"] is False
