"""Unit tests for isolated BingX Sweep Reversion research math."""
from __future__ import annotations

import math

import pytest

from astra_bot.research.bsr.basis import align_cross_venue_trades
from astra_bot.research.bsr.binance_spot_recorder import BinanceSpotRecorder
from astra_bot.research.bsr.bingx_public import BingXPublicAPIError, BingXPublicClient
from astra_bot.research.bsr.economics import (
    CostModel,
    net_pnl,
    position_quantity,
    round_trip_cost_rate,
)
from astra_bot.research.bsr.features import book_metrics, evaluate_signal, trade_flow
from astra_bot.research.bsr.models import BookSnapshot, MarketTrade, SignalConfig
from astra_bot.research.bsr.websocket_recorder import DepthBookState


def _book(ts: int, mid: float, bid_depth: float, ask_depth: float) -> BookSnapshot:
    bid, ask = mid * 0.9999, mid * 1.0001
    return BookSnapshot("BTC-USDT", ts, ((bid, bid_depth / bid),), ((ask, ask_depth / ask),))


def test_book_metrics_calculates_spread_and_imbalance():
    metrics = book_metrics(_book(1000, 100_000, 80_000, 20_000))
    assert metrics["book_imbalance"] == pytest.approx(0.6)
    assert metrics["spread_bps"] == pytest.approx(2.0)


def test_trade_flow_uses_buyer_maker_as_aggressive_sell():
    flow = trade_flow([MarketTrade(1100, 100, 2, True), MarketTrade(1200, 100, 1, False)],
                      1000, 1300)
    assert flow["sell_notional"] == pytest.approx(200)
    assert flow["buy_notional"] == pytest.approx(100)
    assert flow["imbalance"] == pytest.approx(-1 / 3)


def test_long_sweep_recovery_signal():
    before, sweep, recovery = (_book(1000, 100.0, 100_000, 100_000),
                               _book(2000, 99.70, 50_000, 100_000),
                               _book(3000, 99.95, 90_000, 40_000))
    trades = [MarketTrade(ts, 99.8, 30.0, True) for ts in range(2100, 2900, 100)]
    cfg = SignalConfig(min_aggressive_notional=1_000, min_dislocation_bps=3,
                       min_rebound_bps=10, min_score=60, max_snapshot_age_ms=2_000)
    signal = evaluate_signal(before=before, sweep=sweep, recovery=recovery, trades=trades,
                             fair_price=100.10, now_ms=3100, config=cfg)
    assert signal is not None and signal.direction == "LONG"
    assert signal.aggressive_notional == pytest.approx(8 * 99.8 * 30.0)
    assert signal.depth_drawdown > 0.4 and signal.depth_recovery > 0.7


def test_stale_snapshot_is_rejected():
    before, sweep, recovery = (_book(1000, 100, 100_000, 100_000),
                               _book(2000, 99.7, 50_000, 100_000),
                               _book(3000, 99.95, 90_000, 40_000))
    assert evaluate_signal(before=before, sweep=sweep, recovery=recovery, trades=[],
                           fair_price=100, now_ms=10_000) is None


def test_maker_maker_cost_is_four_bps_before_slippage():
    model = CostModel(entry_slippage_rate=0, exit_slippage_rate=0)
    assert round_trip_cost_rate(model) == pytest.approx(0.0004)


def test_signed_funding_rate_is_charged_to_correct_side():
    model = CostModel(entry_slippage_rate=0, exit_slippage_rate=0, funding_rate=0.001)
    assert round_trip_cost_rate(model, is_long=True) == pytest.approx(0.0014)
    assert round_trip_cost_rate(model, is_long=False) == pytest.approx(-0.0006)
    negative = CostModel(entry_slippage_rate=0, exit_slippage_rate=0, funding_rate=-0.001)
    assert round_trip_cost_rate(negative, is_long=True) == pytest.approx(-0.0006)
    assert round_trip_cost_rate(negative, is_long=False) == pytest.approx(0.0014)


def test_taker_taker_cost_is_ten_bps_before_slippage():
    model = CostModel(entry_slippage_rate=0, exit_slippage_rate=0)
    assert round_trip_cost_rate(model, entry_maker=False, exit_maker=False) == pytest.approx(0.001)


def test_net_pnl_includes_fee_and_slippage():
    model = CostModel(maker_fee_rate=0.0002, entry_slippage_rate=0.0001,
                      exit_slippage_rate=0.0001)
    assert net_pnl(notional=10_000, gross_return_rate=0.002, model=model) == pytest.approx(14.0)


def test_position_sizing_respects_risk_and_leverage_caps():
    qty = position_quantity(equity=1_000, risk_fraction=0.003, entry_price=100,
                            stop_price=98, max_leverage=2, cost_rate=0.0006)
    assert qty > 0 and qty * 100 <= 2_000
    assert qty * (2 + 0.06) <= 3.0 + 1e-9


def test_invalid_fair_price_is_rejected():
    book = _book(1000, 100, 100_000, 100_000)
    with pytest.raises(ValueError, match="fair_price"):
        evaluate_signal(before=book, sweep=_book(2000, 99, 50_000, 100_000),
                        recovery=_book(3000, 99.5, 90_000, 40_000), trades=[],
                        fair_price=0, now_ms=3100)


def test_incremental_depth_waits_for_full_snapshot():
    state = DepthBookState()
    update = {"action": "update", "bids": [["100", "2"]], "asks": [["101", "3"]]}
    assert state.apply(update) is False
    assert state.synchronized is False
    snapshot = {"action": "all", "lastUpdateId": 10,
                "bids": [["100", "2"]], "asks": [["101", "3"]]}
    assert state.apply(snapshot) is True
    assert state.bids == {100.0: 2.0}
    update = {"action": "update", "lastUpdateId": 11,
              "bids": [["100", "0"], ["99", "4"]], "asks": [["101", "5"]]}
    assert state.apply(update) is True
    assert state.bids == {99.0: 4.0}
    assert state.asks == {101.0: 5.0}


def test_depth_sequence_gap_invalidates_and_clears_book():
    state = DepthBookState()
    assert state.apply({"action": "all", "lastUpdateId": 10,
                        "bids": [["100", "2"]], "asks": [["101", "3"]]}) is True
    assert state.apply({"action": "update", "lastUpdateId": 12,
                        "bids": [["100", "1"]], "asks": []}) is False
    assert state.synchronized is False
    assert state.last_update_id is None
    assert state.bids == {} and state.asks == {}


def test_duplicate_depth_sequence_invalidates_book():
    state = DepthBookState()
    assert state.apply({"action": "all", "lastUpdateId": 10,
                        "bids": [["100", "2"]], "asks": [["101", "3"]]}) is True
    assert state.apply({"action": "update", "lastUpdateId": 10,
                        "bids": [["100", "1"]], "asks": []}) is False
    assert state.synchronized is False
    assert state.bids == {} and state.asks == {}


def test_depth_update_without_sequence_id_invalidates_book():
    state = DepthBookState()
    assert state.apply({"action": "all", "lastUpdateId": 10,
                        "bids": [["100", "2"]], "asks": [["101", "3"]]}) is True
    assert state.apply({"action": "update", "bids": [["100", "1"]], "asks": []}) is False
    assert state.synchronized is False
    assert state.bids == {} and state.asks == {}


def test_depth_snapshot_without_sequence_id_is_rejected():
    state = DepthBookState()
    assert state.apply({"action": "all", "bids": [["100", "2"]],
                        "asks": [["101", "3"]]}) is False
    assert state.synchronized is False
    assert state.bids == {}


def test_crossed_incremental_book_is_invalidated():
    state = DepthBookState()
    state.apply({"action": "all", "lastUpdateId": 10,
                 "bids": [["100", "2"]], "asks": [["101", "3"]]})
    assert state.apply({"action": "update", "lastUpdateId": 11,
                        "bids": [["102", "2"]], "asks": []}) is False
    assert state.synchronized is False
    assert state.bids == {}



def test_binance_spot_stream_symbol_normalization():
    assert BinanceSpotRecorder._symbol_stream("BTC-USDT") == "btcusdt@trade"
    assert BinanceSpotRecorder._symbol_stream("HYPE-USDT") == "hypeusdt@trade"


def test_binance_spot_stream_rejects_non_usdt_symbol():
    with pytest.raises(ValueError):
        BinanceSpotRecorder._symbol_stream("BTC-USDC")



def test_bingx_recent_trades_rejects_non_boolean_buyer_maker(monkeypatch):
    client = BingXPublicClient()
    monkeypatch.setattr(
        client, "_get",
        lambda *args, **kwargs: [
            {"time": 1, "price": "100", "qty": "1", "buyerMaker": "false"}
        ],
    )
    with pytest.raises(BingXPublicAPIError, match="buyerMaker"):
        client.recent_trades("BTC-USDT")



def test_basis_join_never_uses_future_spot_trade():
    perp = [
        {"symbol": "BTC-USDT", "exchange_time_ms": 1_500, "price": "101"},
    ]
    spot = [
        {"symbol": "BTCUSDT", "exchange_time_ms": 1_000, "price": "100"},
        {"symbol": "BTCUSDT", "exchange_time_ms": 2_000, "price": "110"},
    ]
    rows = align_cross_venue_trades(perp, spot, max_spot_age_ms=1_000)
    assert len(rows) == 1
    assert rows[0].spot_timestamp_ms == 1_000
    assert rows[0].spot_price == pytest.approx(100)
    assert rows[0].basis_bps == pytest.approx(10_000 * math.log(1.01))


def test_basis_join_rejects_stale_or_future_only_spot_data():
    perp = [{"symbol": "ETH-USDT", "exchange_time_ms": 5_000, "price": "2_000"}]
    stale = [{"symbol": "ETHUSDT", "exchange_time_ms": 3_000, "price": "1_999"}]
    future = [{"symbol": "ETHUSDT", "exchange_time_ms": 6_000, "price": "2_001"}]
    assert align_cross_venue_trades(perp, stale, max_spot_age_ms=999) == []
    assert align_cross_venue_trades(perp, future, max_spot_age_ms=10_000) == []



def test_book_snapshot_rejects_unsorted_levels_and_non_finite_values():
    with pytest.raises(ValueError, match="descending"):
        BookSnapshot("BTC-USDT", 1, ((99, 1), (100, 1)), ((101, 1),))
    with pytest.raises(ValueError, match="finite"):
        BookSnapshot("BTC-USDT", 1, ((math.nan, 1),), ((101, 1),))


def test_market_trade_rejects_non_boolean_aggressor_flag():
    with pytest.raises(ValueError, match="buyer_maker"):
        MarketTrade(1, 100, 1, "false")
