from __future__ import annotations

import csv
import json
from decimal import Decimal

from scripts import build_okx_backtest_dataset as builder


def _minute_rows(start: int, count: int) -> list[dict]:
    return [
        {
            "open_time": start + index * builder.MINUTE_MS,
            "open": Decimal("100"),
            "high": Decimal("101"),
            "low": Decimal("99"),
            "close": Decimal("100.5"),
            "volume_contracts": Decimal("1"),
            "volume": Decimal("0.1"),
            "quote_volume": Decimal("10"),
            "confirm": "1",
            "source": "test",
        }
        for index in range(count)
    ]


def test_aggregate_timeframes_builds_complete_15m_and_1h_bars() -> None:
    start = 1_790_784_000_000
    bars, incomplete = builder.aggregate_timeframes(iter(_minute_rows(start, 60)))

    assert len(bars["15m"]) == 4
    assert len(bars["1h"]) == 1
    assert incomplete == {"15m": [], "1h": []}
    assert bars["15m"][0]["open_time"] == start
    assert bars["15m"][0]["volume"] == "1.5"
    assert bars["15m"][0]["quote_volume"] == "150"


def test_aggregate_timeframes_drops_incomplete_buckets_without_imputation() -> None:
    start = 1_790_784_000_000
    rows = _minute_rows(start, 60)
    rows.pop(5)

    bars, incomplete = builder.aggregate_timeframes(iter(rows))

    assert len(bars["15m"]) == 3
    assert len(bars["1h"]) == 0
    assert incomplete["15m"] == [start]
    assert incomplete["1h"] == [start]


def test_build_dataset_writes_portfolio_compatible_files(tmp_path) -> None:
    source = tmp_path / "source"
    month = "2026-10"
    month_dir = source / month
    month_dir.mkdir(parents=True)
    start = 1_790_784_000_000
    rows = _minute_rows(start, 60)
    candle_path = month_dir / f"BTC-USDT-SWAP_1m_{month}.csv"
    with candle_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "open_time", "open", "high", "low", "close", "volume_contracts",
            "volume_base", "volume_quote", "confirm", "source",
        ])
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "open_time": row["open_time"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume_contracts": row["volume_contracts"],
                "volume_base": row["volume"],
                "volume_quote": row["quote_volume"],
                "confirm": row["confirm"],
                "source": row["source"],
            })
    funding_path = month_dir / f"BTC-USDT-SWAP_funding_archive_{month}.csv"
    with funding_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["funding_time", "funding_rate"])
        writer.writeheader()
        writer.writerow({"funding_time": start, "funding_rate": "0.0001"})
    manifest = {
        "months": [month],
        "common_window_start_ms": start,
        "common_window_end_ms": start + 60 * builder.MINUTE_MS - 1,
        "symbols": {"BTC-USDT-SWAP": {}},
    }
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    output = builder.build_dataset(source, tmp_path / "output")

    assert output["symbols"]["BTC-USDT"]["15m"]["rows"] == 4
    assert output["symbols"]["BTC-USDT"]["1h"]["rows"] == 1
    assert (tmp_path / "output" / "BTCUSDT_15m.csv").exists()
    assert (tmp_path / "output" / "BTCUSDT_1h.csv").exists()
    with (tmp_path / "output" / "funding_BTCUSDT.csv").open(newline="", encoding="utf-8") as handle:
        funding = list(csv.DictReader(handle))
    assert funding[0]["symbol"] == "BTC-USDT"
    assert funding[0]["rate"] == "0.0001"
