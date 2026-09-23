"""Sprint 2026-09-23: p_win calibration must not treat confidence as win-rate."""

from astra_bot.decision.p_win_calibration import calibrate_p_win


def test_calibrate_caps_below_raw_confidence():
    p = calibrate_p_win("momentum", confidence=0.95)
    assert 0.05 <= p <= 0.55
    assert p < 0.95


def test_zeus_prior_slightly_higher():
    z = calibrate_p_win("zeus_wedge_retest_4h", confidence=0.5)
    m = calibrate_p_win("scalp5m", confidence=0.5)
    assert z >= m


def test_symbol_loss_guard_pause():
    from datetime import datetime, timezone

    from astra_bot.core.kill_switch import SymbolLossGuard

    g = SymbolLossGuard(max_consecutive=3, pause_hours=4.0)
    now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
    assert not g.is_paused("BTC-USDT", now)
    g.record("BTC-USDT", -1.0, now)
    g.record("BTC-USDT", -1.0, now)
    assert not g.is_paused("BTC-USDT", now)
    g.record("BTC-USDT", -1.0, now)
    assert g.is_paused("BTC-USDT", now)
