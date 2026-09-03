"""Тесты хелперов scripts/fetch_klines.py (без сети)."""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from fetch_klines import (
    iter_months,
    parse_klines_csv,
    vision_monthly_url,
)


def test_vision_monthly_url_format():
    url = vision_monthly_url("ETHUSDT", "4h", 2024, 1)
    assert url == (
        "https://data.binance.vision/data/spot/monthly/klines/"
        "ETHUSDT/4h/ETHUSDT-4h-2024-01.zip"
    )
    assert vision_monthly_url("btcusdt", "1h", 2026, 12).endswith(
        "BTCUSDT-1h-2026-12.zip"
    )


def test_iter_months_covers_range():
    months = list(iter_months(datetime(2024, 11, 1, tzinfo=UTC), datetime(2025, 2, 28, tzinfo=UTC)))
    assert months == [(2024, 11), (2024, 12), (2025, 1), (2025, 2)]


def test_parse_klines_csv_keeps_only_data_rows():
    text = (
        "open_time,open,high,low,close,volume,close_time,quote_volume,ignore\n"
        "1724112000000,2636.3,2676.75,2621.56,2661.98,6102.21,1724126399999,16194046.89,0\n"
        "1724126400000,2661.98,2694.99,2657.81,2674.82,7863.85,1724140799999,21031519.35,0\n"
        "\n"
        "# мусорная строка\n"
    )
    rows = parse_klines_csv(text)
    assert len(rows) == 2
    assert rows[0].startswith("1724112000000")
    assert rows[1].split(",")[4] == "2674.82"
