"""Unit tests for isolated BingX Sweep Reversion research math."""
from __future__ import annotations
import pytest
from astra_bot.research.bsr.economics import CostModel, net_pnl, position_quantity, round_trip_cost_rate
from astra_bot.research.bsr.features import book_metrics, evaluate_signal, trade_flow
from astra_bot.research.bsr.models import BookSnapshot, MarketTrade, SignalConfig

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
