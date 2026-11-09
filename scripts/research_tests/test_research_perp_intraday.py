import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_perp_intraday import (
    INTERVAL_MS,
    Trade,
    build_frame,
    fetch_funding,
    fetch_klines,
    fetch_mark_prices,
    funding_cashflow,
    latest_contiguous_funding_segment,
    metrics,
    net_trade_return,
    request_json,
    resolve_barrier_exit,
    run_backtest,
    validate_market_data,
    write_report,
)


def test_funding_history_uses_bounded_30_day_windows(monkeypatch):
    import research_perp_intraday as research

    calls = []
    def fake_request(_path, params):
        calls.append(params)
        return [{
            "fundingTime": params["startTime"],
            "fundingRate": "0.0001",
            "markPrice": "100",
        }]

    monkeypatch.setattr(research, "request_json", fake_request)
    chunk_ms = 30 * 24 * 60 * 60 * 1000
    start_ms = 1_700_000_000_000
    rows = fetch_funding("BTC-USDT", start_ms, start_ms + chunk_ms + 1000)
    assert len(rows) == 2
    assert len(calls) == 2
    assert calls[0]["startTime"] == start_ms
    assert calls[0]["endTime"] == start_ms + chunk_ms - 1
    assert calls[1]["startTime"] == start_ms + chunk_ms


def test_candle_loader_pages_1000_bar_windows_and_skips_only_leading_empty(monkeypatch):
    import research_perp_intraday as research

    interval = INTERVAL_MS
    start_ms = 1_700_000_000_000 - (1_700_000_000_000 % interval)
    available_start = start_ms + 2 * interval
    end_ms = start_ms + 6 * interval + 1
    calls = []

    def fake_request(_path, params):
        calls.append(params)
        return [
            [
                ts, "100", "101", "99", "100", "10",
                ts + interval - 1, "1000", 10, "5", "500",
            ]
            for ts in range(params["startTime"], params["endTime"] + 1, interval)
            if ts >= available_start
        ][:params["limit"]]

    monkeypatch.setattr(research, "PAGE_KLINES", 2)
    monkeypatch.setattr(research, "request_json", fake_request)
    rows = fetch_klines("BTC-USDT", start_ms, end_ms)
    assert [row[0] for row in rows] == list(range(available_start, start_ms + 6 * interval, interval))
    assert len(calls) == 3


def test_mark_price_loader_pages_bounded_4h_windows(monkeypatch):
    import research_perp_intraday as research

    interval = 4 * 60 * 60 * 1000
    base = 1_700_000_000_000 - (1_700_000_000_000 % interval)
    end_ms = base + 5 * interval + 1
    calls = []

    def fake_request(_path, params):
        calls.append(params)
        return [
            [ts, "100"]
            for ts in range(params["startTime"], params["endTime"] + 1, interval)
        ][:params["limit"]]

    monkeypatch.setattr(research, "PAGE_KLINES", 2)
    monkeypatch.setattr(research, "request_json", fake_request)
    prices = fetch_mark_prices("BTC-USDT", base, end_ms)
    assert len(prices) == 6
    assert len(calls) == 3
def test_latest_funding_segment_excludes_gaps_without_imputation():
    funding = [
        {"fundingTime": 0, "fundingRate": "0.0001", "markPrice": "100"},
        {"fundingTime": 8 * 3_600_000, "fundingRate": "0.0001", "markPrice": "100"},
        {"fundingTime": 24 * 3_600_000, "fundingRate": "-0.0001", "markPrice": "100"},
        {"fundingTime": 32 * 3_600_000, "fundingRate": "0.0001", "markPrice": "100"},
    ]
    segment, excluded = latest_contiguous_funding_segment(funding)
    assert len(excluded) == 2
    assert [row["fundingTime"] for row in segment] == [24 * 3_600_000, 32 * 3_600_000]
def test_bingx_100410_uses_four_attempt_cooldown(monkeypatch):
    import research_perp_intraday as research

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

    attempts = []
    delays = []
    def fake_urlopen(_request, timeout):
        attempts.append(timeout)
        if len(attempts) < 4:
            return FakeResponse({"code": 100410, "msg": "retry later"})
        return FakeResponse({"code": 0, "data": [{"ok": True}]})

    monkeypatch.setattr(research.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(research, "_last_request_monotonic", 0.0)
    monkeypatch.setattr(research.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(research.time, "sleep", delays.append)
    monkeypatch.setattr(research, "API_MIN_INTERVAL_S", 0)
    assert request_json("/test", {}) == [{"ok": True}]
    assert len(attempts) == 4
    assert delays == [5, 30, 60]
def test_positive_funding_is_paid_by_long_and_received_by_short():
    assert funding_cashflow(1, 0.001, 100.0, 100.0) == -0.001
    assert funding_cashflow(-1, 0.001, 100.0, 100.0) == 0.001
    assert funding_cashflow(1, -0.001, 100.0, 100.0) == 0.001


def test_flat_trade_is_negative_after_fees_and_adverse_slippage():
    net, fees, price_return = net_trade_return(
        side=1,
        entry_raw=100.0,
        exit_raw=100.0,
        slippage=0.001,
        fee_per_side=0.0005,
        funding_return=0.0,
    )
    assert net < 0
    assert fees > 0
    assert price_return < 0


def test_same_bar_stop_and_target_uses_stop_first_for_both_sides():
    assert resolve_barrier_exit(1, 100.0, 103.0, 97.0, 98.0, 102.0) == (98.0, "stop")
    assert resolve_barrier_exit(-1, 100.0, 103.0, 97.0, 102.0, 98.0) == (102.0, "stop")


def _synthetic_frame(bar_count: int = 22) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=bar_count, freq="30min", tz="UTC")
    frame = pd.DataFrame(index=index)
    frame["open"] = 100.0
    frame["high"] = 100.3
    frame["low"] = 99.8
    frame["close"] = 100.1
    frame["atr"] = 1.0
    frame["bb_mid"] = 100.0
    return frame


def test_no_progress_exit_happens_after_one_completed_30m_bar(monkeypatch):
    import research_perp_intraday as research

    frame = _synthetic_frame(5)
    masks = {
        (name, side): pd.Series(False, index=frame.index)
        for name in research.STRATEGIES
        for side in (1, -1)
    }
    masks[("trend_pullback", 1)].iloc[0] = True
    monkeypatch.setattr(research, "signal_masks", lambda _: masks)
    trades = run_backtest("BTC-USDT", frame, [], "trend_pullback", 1, 0.001)
    assert len(trades) == 1
    assert trades[0].exit_reason == "no_progress_30m"
    assert trades[0].hold_minutes == 30


def test_hard_hold_cap_is_nine_hours(monkeypatch):
    import research_perp_intraday as research

    frame = _synthetic_frame(24)
    frame["high"] = 101.0  # MFE clears 0.35R, so the no-progress exit does not fire.
    frame["low"] = 99.9
    frame["close"] = 100.4
    masks = {
        (name, side): pd.Series(False, index=frame.index)
        for name in research.STRATEGIES
        for side in (1, -1)
    }
    masks[("trend_pullback", 1)].iloc[0] = True
    monkeypatch.setattr(research, "signal_masks", lambda _: masks)
    trades = run_backtest("BTC-USDT", frame, [], "trend_pullback", 1, 0.001)
    assert len(trades) == 1
    assert trades[0].exit_reason == "max_hold_9h"
    assert trades[0].hold_minutes == 9 * 60


def test_metrics_pf_uses_net_trade_results():
    trades = [
        Trade(
            "BTC-USDT", "trend_pullback", "LONG",
            "2026-01-01T00:00:00+00:00", "2026-01-01T00:30:00+00:00",
            30, 100, 102, 98, 103, "take", 2, 2, 0.1, 0.1, 0, 1.8, 0.90, 0.15,
        ),
        Trade(
            "BTC-USDT", "trend_pullback", "SHORT",
            "2026-01-01T01:00:00+00:00", "2026-01-01T01:30:00+00:00",
            30, 100, 101, 102, 97, "stop", 2, -1, 0.1, 0.1, 0, -1.2, -0.60, 0.15,
        ),
    ]
    result = metrics(trades)
    assert math.isclose(result["pf"], 1.8 / 1.2)
    assert result["n"] == 2


def test_write_report_serializes_nested_strategy_trade_map(tmp_path):
    trade = Trade(
        "BTC-USDT", "trend_pullback", "LONG",
        "2026-01-01T00:00:00+00:00", "2026-01-01T00:30:00+00:00",
        30, 100, 101, 98, 103, "take", 2, 1, 0.1, 0.1, 0,
        0.8, 0.4, 0.15,
    )
    result = {
        "base_30bps_rt": {
            "BTC-USDT": {
                name: [trade] if name == "trend_pullback" else []
                for name in ("trend_pullback", "trend_expansion", "range_reversion")
            }
        },
        "stress_40bps_rt": {
            "BTC-USDT": {
                name: []
                for name in ("trend_pullback", "trend_expansion", "range_reversion")
            }
        },
    }
    manifest = {
        "data_cutoff_utc": "2026-11-09T00:00:00+00:00",
        "symbols": {
            "BTC-USDT": {
                "first_bar_utc": "2021-01-01T00:00:00+00:00",
                "last_bar_utc": "2026-11-08T23:30:00+00:00",
                "bars": 100000,
                "funding_events": 5000,
            }
        },
    }
    write_report(tmp_path, manifest, result)
    trades = pd.read_csv(tmp_path / "trades.csv")
    assert len(trades) == 1
    assert trades.iloc[0]["strategy"] == "trend_pullback"
    assert trades.iloc[0]["slippage_pct"] == 0.1
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Aggregate IS and OOS" in report
    assert "Trade-sequence DD proxy" in report
    assert "mean holding <= 90 minutes" in report
    assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["symbols"]["BTC-USDT"]["bars"] == 100000


def _valid_market_fixture(count=500):
    start = 1_700_000_000_000
    start -= start % INTERVAL_MS
    klines = []
    for index in range(count):
        ts = start + index * INTERVAL_MS
        klines.append([
            ts, "100", "101", "99", "100", "10", ts + INTERVAL_MS - 1,
            "1000", 10, "5", "500",
        ])
    funding = []
    funding_ts = start + 8 * 60 * 60 * 1000
    end = start + count * INTERVAL_MS
    while funding_ts < end:
        funding.append({"fundingTime": funding_ts, "fundingRate": "0.0001", "markPrice": "100"})
        funding_ts += 8 * 60 * 60 * 1000
    return start, klines, funding, end


def test_market_validation_requires_usdt_perpetual_and_complete_funding():
    _, klines, funding, end = _valid_market_fixture(count=501)
    contract = {"currency": "USDT", "status": 1, "launchTime": 0}
    validate_market_data("BTC-USDT", contract, klines, funding, end)
    with pytest.raises(ValueError, match="not a USDT-margined perpetual"):
        validate_market_data(
            "BTC-USD",
            {**contract, "currency": "USD"},
            klines,
            funding,
            end,
        )
    with pytest.raises(ValueError, match="missing or irregular 30m intervals"):
        validate_market_data("BTC-USDT", contract, klines[:10] + klines[11:], funding, end)


def test_contract_launch_time_prevents_pre_listing_prices():
    _, klines, funding, end = _valid_market_fixture()
    contract = {
        "currency": "USDT",
        "status": 1,
        "launchTime": int(klines[1][0]),
    }
    with pytest.raises(ValueError, match="predates BingX launchTime"):
        validate_market_data("BTC-USDT", contract, klines, funding, end)


def test_hourly_context_is_never_from_a_future_hour():
    rows = []
    start = 1_700_000_000_000
    start -= start % INTERVAL_MS
    for index in range(100):
        ts = start + index * INTERVAL_MS
        price = 100 + index * 0.01
        rows.append([
            ts, price, price + 0.2, price - 0.2, price + 0.05, 10,
            ts + INTERVAL_MS - 1, 1000, 10, 5, 500,
        ])
    frame = build_frame(rows)
    base = frame.reset_index()
    assert (base["available_at"].isna() | (base["available_at"] <= base["decision_at"])).all()
    assert pd.isna(frame.iloc[0]["htf_close"])
    assert pd.notna(frame.iloc[1]["htf_close"])


def test_base_and_stress_cost_scenarios_share_same_cost_eligibility(monkeypatch):
    import research_perp_intraday as research

    frame = _synthetic_frame(5)
    frame["atr"] = 0.5  # 0.675% stop width; fails the frozen 0.40R stress-cost gate.
    masks = {
        (name, side): pd.Series(False, index=frame.index)
        for name in research.STRATEGIES
        for side in (1, -1)
    }
    masks[("trend_pullback", 1)].iloc[0] = True
    monkeypatch.setattr(research, "signal_masks", lambda _: masks)
    base = run_backtest("BTC-USDT", frame, [], "trend_pullback", 1, research.BASE_SLIPPAGE_PER_SIDE)
    stress = run_backtest("BTC-USDT", frame, [], "trend_pullback", 1, research.STRESS_SLIPPAGE_PER_SIDE)
    assert base == []
    assert stress == []
