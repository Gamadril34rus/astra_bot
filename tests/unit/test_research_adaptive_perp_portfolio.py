from __future__ import annotations

import csv
from types import SimpleNamespace

import pandas as pd
import pytest
import scripts.archive_bingx_funding_history as funding_archive
import scripts.archive_bingx_usdm_candles as candle_archive
import scripts.research_adaptive_perp_portfolio as research
from scripts.archive_bingx_funding_history import _rows_from_response
from scripts.research_adaptive_perp_portfolio import (
    MAX_HOLD_BARS,
    add_features,
    choose_signal,
    common_15m_coverage_window,
    funding_cost_fraction,
    latest_contiguous_funding_segment,
    load_bars,
    simulate_symbol,
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


def test_archive_page_size_matches_verified_bingx_adapter_cap() -> None:
    assert candle_archive.PAGE_SIZE == 500

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
async def test_funding_archive_retries_bingx_100410_with_cooldowns(monkeypatch) -> None:
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    class CooldownError(RuntimeError):
        code = 100410

    class CooldownClient:
        def __init__(self):
            self.attempts = 0

        async def call(self):
            self.attempts += 1
            if self.attempts < 4:
                raise CooldownError("BingX API code 100410")
            return {"code": 0, "data": []}

    monkeypatch.setattr(funding_archive.asyncio, "sleep", fake_sleep)
    client = CooldownClient()
    result = await funding_archive._call_with_cooldown(client.call, label="funding test")
    assert result == {"code": 0, "data": []}
    assert client.attempts == 4
    assert delays == [5.0, 30.0, 60.0]

def test_candle_archive_uses_latest_contiguous_funding_start(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(candle_archive, "ARCHIVE_DIR", tmp_path)
    path = tmp_path / "funding_BTCUSDT.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("funding_time", "rate"))
        writer.writeheader()
        writer.writerows([
            {"funding_time": 0, "rate": "0.0001"},
            {"funding_time": 8 * 3_600_000, "rate": "0.0001"},
            {"funding_time": 100 * 3_600_000, "rate": "-0.0001"},
            {"funding_time": 108 * 3_600_000, "rate": "0.0002"},
        ])
    assert candle_archive._latest_contiguous_funding_start_ms("BTC-USDT", 0) == 100 * 3_600_000
    assert candle_archive._latest_contiguous_funding_start_ms("BTC-USDT", 200 * 3_600_000) == 200 * 3_600_000

@pytest.mark.asyncio
async def test_initial_archive_backfills_prefix_left_by_partial_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(candle_archive, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(candle_archive, "PAGE_SIZE", 2)
    interval = 900_000
    bars = [
        SimpleNamespace(
            open_time=ts, open=100, high=102, low=99, close=101,
            volume=10, quote_volume=1000, trades_count=1,
        )
        for ts in range(0, 4 * interval, interval)
    ]
    path = candle_archive._archive_path("BTC-USDT", "15m")
    candle_archive._write_rows(path, {
        int(bar.open_time): candle_archive._row_from_candle(bar)
        for bar in bars[2:]
    })

    class RangedPageClient:
        async def fetch_kline_page(
            self, _symbol, _timeframe, *, start_time_ms, end_time_ms, limit
        ):
            return [
                bar for bar in bars
                if start_time_ms <= bar.open_time <= end_time_ms
            ][:limit]

    report, _ = await candle_archive._download_pair(
        RangedPageClient(),
        symbol="BTC-USDT",
        timeframe="15m",
        mode="initial",
        start_ms=0,
        now_ms=4 * interval + 1,
        launch_ms=0,
        funding_start_ms=0,
    )
    assert report["first_open_time"] == 0
    assert report["new_bars"] == 2
    assert report["verified_overlap_bars"] == 2

@pytest.mark.asyncio
async def test_initial_archive_discards_pre_funding_rows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(candle_archive, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(candle_archive, "PAGE_SIZE", 2)
    interval = 900_000
    bars = [
        SimpleNamespace(
            open_time=ts, open=100, high=102, low=99, close=101,
            volume=10, quote_volume=1000, trades_count=1,
        )
        for ts in range(4 * interval, 8 * interval, interval)
    ]
    path = candle_archive._archive_path("BTC-USDT", "15m")
    candle_archive._write_rows(path, {
        int(ts): candle_archive._row_from_candle(
            SimpleNamespace(
                open_time=ts, open=100, high=102, low=99, close=101,
                volume=10, quote_volume=1000, trades_count=1,
            )
        )
        for ts in (0, interval, 2 * interval)
    })

    class RangedPageClient:
        async def fetch_kline_page(
            self, _symbol, _timeframe, *, start_time_ms, end_time_ms, limit
        ):
            return [
                bar for bar in bars
                if start_time_ms <= bar.open_time <= end_time_ms
            ][:limit]

    report, _ = await candle_archive._download_pair(
        RangedPageClient(),
        symbol="BTC-USDT",
        timeframe="15m",
        mode="initial",
        start_ms=0,
        now_ms=8 * interval + 1,
        launch_ms=0,
        funding_start_ms=4 * interval,
    )
    assert report["first_open_time"] == 4 * interval
    assert report["bar_count"] == 4
    assert report["gap_count"] == 0

@pytest.mark.asyncio
async def test_archive_retries_bingx_100410_with_cooldowns(monkeypatch) -> None:
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    class CooldownError(RuntimeError):
        code = 100410

    class CooldownClient:
        def __init__(self):
            self.attempts = 0

        async def call(self):
            self.attempts += 1
            if self.attempts < 4:
                raise CooldownError("BingX API code 100410")
            return ["ok"]

    monkeypatch.setattr(candle_archive.asyncio, "sleep", fake_sleep)
    client = CooldownClient()
    result = await candle_archive._call_with_cooldown(client.call, label="test")
    assert result == ["ok"]
    assert client.attempts == 4
    assert delays == [5.0, 30.0, 60.0]

@pytest.mark.asyncio
async def test_archive_persists_verified_pages_before_later_cooldown_failure(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(candle_archive, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(candle_archive, "PAGE_SIZE", 2)

    async def fake_sleep(_delay):
        return None

    monkeypatch.setattr(candle_archive.asyncio, "sleep", fake_sleep)
    interval = 900_000
    bars = [
        SimpleNamespace(
            open_time=ts, open=100, high=102, low=99, close=101,
            volume=10, quote_volume=1000, trades_count=1,
        )
        for ts in range(0, 6 * interval, interval)
    ]

    class CooldownError(RuntimeError):
        code = 100410

    class FailingSecondPageClient:
        async def fetch_kline_page(
            self, _symbol, _timeframe, *, start_time_ms, end_time_ms, limit
        ):
            if start_time_ms >= 2 * interval:
                raise CooldownError("BingX API code 100410")
            return [bar for bar in bars if start_time_ms <= bar.open_time <= end_time_ms][:limit]

    with pytest.raises(CooldownError):
        await candle_archive._download_pair(
            FailingSecondPageClient(),
            symbol="BTC-USDT",
            timeframe="15m",
            mode="initial",
            start_ms=0,
            now_ms=6 * interval + 1,
            launch_ms=0,
            funding_start_ms=0,
        )

    saved = candle_archive._read_rows(candle_archive._archive_path("BTC-USDT", "15m"))
    assert list(saved) == [0, interval]


@pytest.mark.asyncio
async def test_candle_archive_uses_bounded_forward_pages(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(candle_archive, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(candle_archive, "PAGE_SIZE", 3)
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
        for ts in range(0, 6 * 900_000, 900_000)
    ]

    class RangedPageClient:
        def __init__(self) -> None:
            self.ranges: list[tuple[int, int]] = []

        async def fetch_kline_page(
            self, _symbol, _timeframe, *, start_time_ms, end_time_ms, limit
        ):
            self.ranges.append((start_time_ms, end_time_ms))
            return [
                bar for bar in bars
                if start_time_ms <= bar.open_time <= end_time_ms
            ][:limit]

    client = RangedPageClient()
    report, _ = await candle_archive._download_pair(
        client,
        symbol="BTC-USDT",
        timeframe="15m",
        mode="initial",
        start_ms=0,
        now_ms=6 * 900_000 + 1,
        launch_ms=0,
    )
    assert report["pages_requested"] == 2
    assert report["bar_count"] == 6
    assert client.ranges[0][0] == 0
    assert client.ranges[1][0] == 3 * 900_000

@pytest.mark.asyncio
async def test_candle_archive_skips_only_leading_empty_windows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(candle_archive, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(candle_archive, "PAGE_SIZE", 2)
    bars = [
        SimpleNamespace(
            open_time=ts, open=100, high=102, low=99, close=101,
            volume=10, quote_volume=1000, trades_count=1,
        )
        for ts in range(4 * 900_000, 8 * 900_000, 900_000)
    ]

    class RangedPageClient:
        async def fetch_kline_page(
            self, _symbol, _timeframe, *, start_time_ms, end_time_ms, limit
        ):
            return [
                bar for bar in bars
                if start_time_ms <= bar.open_time <= end_time_ms
            ][:limit]

    report, _ = await candle_archive._download_pair(
        RangedPageClient(),
        symbol="BTC-USDT",
        timeframe="15m",
        mode="initial",
        start_ms=0,
        now_ms=8 * 900_000 + 1,
        launch_ms=0,
    )
    assert report["pages_requested"] == 4
    assert report["bar_count"] == 4
    assert report["first_open_time"] == 4 * 900_000

@pytest.mark.asyncio
async def test_no_progress_exit_closes_after_30_minutes(monkeypatch) -> None:
    times = [index * 900_000 for index in range(160)]
    frame = pd.DataFrame({
        "open_time": times,
        "decision_time": [value + 900_000 for value in times],
        "open": [100.0] * 160,
        "high": [100.2] * 160,
        "low": [99.5] * 160,
        "close": [100.1] * 160,
        "atr14": [1.0] * 160,
    })

    def frozen_signal(_frame, index):
        if index == 120:
            return {"family": "TEST", "side": 1, "stop": 99.0, "target_rr": 1.5}
        return None

    monkeypatch.setattr(research, "choose_signal", frozen_signal)
    funding = pd.DataFrame({
        "funding_time": list(range(0, 40 * 3_600_000 + 1, 8 * 3_600_000)),
        "rate": [0.0] * 6,
    })
    diagnostics = {}
    trades = simulate_symbol(
        "BTC-USDT", frame, funding, fee_bps=0, spread_bps=0, slippage_bps=0,
        diagnostics=diagnostics,
    )
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "no_progress_30m"
    assert trades[0]["hold_minutes"] == pytest.approx(30.0)
    assert trades[0]["stress_cost_to_r"] == pytest.approx(0.2475)
    assert diagnostics["raw_signals"] == 1
    assert diagnostics["completed_trades"] == 1
    assert diagnostics["completed_by_family"] == {"TEST": 1}


@pytest.mark.asyncio
async def test_30m_signal_uses_30m_and_90m_invalidation_windows(monkeypatch) -> None:
    interval = 30 * 60_000
    times = [index * interval for index in range(160)]
    frame = pd.DataFrame({
        "open_time": times,
        "decision_time": [value + interval for value in times],
        "open": [100.0] * 160,
        "high": [100.2] * 160,
        "low": [99.5] * 160,
        "close": [100.1] * 160,
        "atr14": [1.0] * 160,
    })

    def frozen_signal(_frame, index):
        if index == 120:
            return {"family": "TEST", "side": 1, "stop": 99.0, "target_rr": 1.5}
        return None

    monkeypatch.setattr(research, "choose_signal", frozen_signal)
    funding = pd.DataFrame({
        "funding_time": list(range(0, 100 * 3_600_000 + 1, 8 * 3_600_000)),
        "rate": [0.0] * 13,
    })
    trades = simulate_symbol(
        "BTC-USDT", frame, funding, fee_bps=0, spread_bps=0, slippage_bps=0,
        bar_minutes=30,
    )
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "no_progress_30m"
    assert trades[0]["hold_minutes"] == pytest.approx(30.0)


def test_common_15m_window_uses_overlapping_symbol_history() -> None:
    coverage = {
        "BTC-USDT": {"15m_first": 1_000, "15m_last": 100_000_000},
        "ETH-USDT": {"15m_first": 2_000, "15m_last": 90_000_000},
        "SOL-USDT": {"15m_first": 3_000, "15m_last": 95_000_000},
    }
    result = common_15m_coverage_window(coverage)
    assert result["common_start_utc"] == pd.to_datetime(3_000, unit="ms", utc=True).isoformat()
    assert result["common_end_utc"] == pd.to_datetime(90_000_000, unit="ms", utc=True).isoformat()
    assert result["covers_12_months"] is False

def test_common_window_accepts_30m_coverage_fields() -> None:
    coverage = {
        "BTC-USDT": {"30m_first": 1_000, "30m_last": 100_000_000},
        "ETH-USDT": {"30m_first": 2_000, "30m_last": 90_000_000},
        "HYPE-USDT": {"30m_first": 3_000, "30m_last": 95_000_000},
    }
    result = common_15m_coverage_window(coverage, "30m")
    assert result["common_start_utc"] == pd.to_datetime(3_000, unit="ms", utc=True).isoformat()
    assert result["common_end_utc"] == pd.to_datetime(90_000_000, unit="ms", utc=True).isoformat()


@pytest.mark.asyncio
async def test_no_progress_90m_exit_closes_stagnant_position(monkeypatch) -> None:
    times = [index * 900_000 for index in range(160)]
    frame = pd.DataFrame({
        "open_time": times,
        "decision_time": [value + 900_000 for value in times],
        "open": [100.0] * 160,
        "high": [100.5] * 160,
        "low": [99.5] * 160,
        "close": [100.2] * 160,
        "atr14": [1.0] * 160,
    })

    def frozen_signal(_frame, index):
        if index == 120:
            return {"family": "TEST", "side": 1, "stop": 99.0, "target_rr": 1.5}
        return None

    monkeypatch.setattr(research, "choose_signal", frozen_signal)
    funding = pd.DataFrame({
        "funding_time": list(range(0, 40 * 3_600_000 + 1, 8 * 3_600_000)),
        "rate": [0.0] * 6,
    })
    trades = simulate_symbol(
        "BTC-USDT", frame, funding, fee_bps=0, spread_bps=0, slippage_bps=0
    )
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "no_progress_90m"
    assert trades[0]["hold_minutes"] == pytest.approx(90.0)
def test_latest_funding_segment_excludes_gaps_without_imputation() -> None:
    frame = pd.DataFrame({
        "funding_time": [0, 8 * 3_600_000, 100 * 3_600_000, 108 * 3_600_000],
        "rate": [0.0001, 0.0002, -0.0001, 0.0003],
    })
    segment, excluded = latest_contiguous_funding_segment(frame)
    assert excluded == 2
    assert segment["funding_time"].tolist() == [100 * 3_600_000, 108 * 3_600_000]


def test_false_break_reversal_fades_failed_range_escape() -> None:
    frame = pd.DataFrame({
        "open": [100.0] * 22,
        "high": [101.0] * 22,
        "low": [99.0] * 22,
        "close": [100.0] * 22,
        "ema20": [100.0] * 22,
        "ema50": [100.0] * 22,
        "atr14": [1.0] * 22,
        "bb_width_pct": [0.5] * 22,
        "volume_ratio": [1.0] * 22,
        "h1_adx14": [15.0] * 22,
        "h1_ema20": [100.0] * 22,
        "h1_ema50": [100.0] * 22,
        "h1_ema20_rising": [False] * 22,
        "h1_ema20_falling": [False] * 22,
        "bb_lower": [98.0] * 22,
        "bb_upper": [102.0] * 22,
        "rsi14": [50.0] * 22,
    })
    frame.loc[20, ["high", "close", "volume_ratio"]] = [104.0, 103.0, 2.0]
    frame.loc[21, ["high", "close"]] = [103.0, 100.5]
    signal = choose_signal(frame, 21)
    assert signal is not None
    assert signal["family"] == "FALSE_BREAK_REVERSAL"
    assert signal["side"] == -1
    assert signal["target_price"] == pytest.approx(100.0)
