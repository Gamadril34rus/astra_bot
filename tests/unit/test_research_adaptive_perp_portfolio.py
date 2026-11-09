from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

import scripts.archive_bingx_usdm_candles as candle_archive
from scripts.archive_bingx_funding_history import _rows_from_response
from scripts.research_adaptive_perp_portfolio import (
    MAX_HOLD_BARS,
    add_features,
    funding_cost_fraction,
    load_bars,
)


def _bars(times: list[int], interval_ms: int) -> pd.DataFrame:
    frame = pd.DataFrame({
        "open_time": times,
        "open": [100.0 + i * 0.01 for i in range(len(times))],
        "high": [102.0 + i * 0.01 for i in range(len(times))],
        "low": [99.0 + i * 0.01 for i in range(len(times))],
        "close": [101.0 + i * 0.01 for i in range(len(times))],
        "volume": [10.0] * len(times),
        "quote_volume": [1000.0] * len(times),
    })
    frame["decision_time"] = frame["open_time"] + interval_ms
    return frame


def test_higher_timeframe_merge_uses_last_closed_hour_only() -> None:
    m15 = _bars([0, 900_000, 1_800_000, 2_700_000, 3_600_000], 900_000)
    h1 = _bars([0, 3_600_000, 7_200_000], 3_600_000)
    merged = add_features(m15, h1)
    assert merged.loc[0:2, "h1_ema20"].isna().all()
    assert pd.notna(merged.loc[3, "h1_ema20"])


def test_positive_funding_is_cost_for_long_and_credit_for_short() -> None:
    rates = pd.Series([0.0001, 0.0002])
    assert funding_cost_fraction(1, rates) == pytest.approx(0.0003)
    assert funding_cost_fraction(-1, rates) == pytest.approx(-0.0003)


def test_hard_hold_limit_is_nine_hours() -> None:
    assert MAX_HOLD_BARS * 15 == 540


def test_loader_rejects_candle_gaps(tmp_path) -> None:
    path = tmp_path / "BTCUSDT_15m.csv"
    _bars([0, 900_000, 2_700_000], 900_000).to_csv(path, index=False)
    with pytest.raises(ValueError, match="gaps"):
        load_bars(path, "15m")


def test_loader_rejects_duplicate_timestamps(tmp_path) -> None:
    path = tmp_path / "BTCUSDT_15m.csv"
    _bars([0, 900_000, 900_000], 900_000).to_csv(path, index=False)
    with pytest.raises(ValueError, match="duplicate"):
        load_bars(path, "15m")



def test_funding_parser_reads_bingx_funding_records() -> None:
    rows = _rows_from_response({
        "code": 0,
        "data": [
            {"symbol": "BTC-USDT", "fundingTime": 1_700_000_000_000, "fundingRate": "0.0001"}
        ],
    })
    assert rows == [{"funding_time": 1_700_000_000_000, "rate": "0.0001"}]


def test_funding_parser_rejects_incomplete_record() -> None:
    with pytest.raises(ValueError, match="missing time/rate"):
        _rows_from_response({"code": 0, "data": [{"fundingTime": 1_700_000_000_000}]})


@pytest.mark.asyncio
async def test_candle_archive_continues_after_short_nonempty_page(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(candle_archive, "ARCHIVE_DIR", tmp_path)
    bars = [
        SimpleNamespace(
            open_time=ts,
            open=100,
            high=102,
            low=99,
            close=101,
            volume=10,
            quote_volume=1000,
            trades_count=1,
        )
        for ts in (1_000_000, 1_900_000, 2_800_000, 3_700_000, 4_600_000, 5_500_000)
    ]

    class ShortPageClient:
        def __init__(self) -> None:
            self.pages = [bars[3:], bars[:3], []]

        async def fetch_kline_page(self, *_args, **_kwargs):
            return self.pages.pop(0)

    report, _ = await candle_archive._download_pair(
        ShortPageClient(),
        symbol="BTC-USDT",
        timeframe="15m",
        mode="initial",
        start_ms=0,
        now_ms=10_000_000,
    )
    assert report["pages_requested"] == 3
    assert report["bar_count"] == 6
