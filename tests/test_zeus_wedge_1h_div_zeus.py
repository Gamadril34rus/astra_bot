"""K-DIV: zeus-like divergence flag independent of legacy div."""
from __future__ import annotations

from astra_bot import zeus_wedge_1h_core as core
from astra_bot.zeus_wedge_1h_core import _rsi_divergence, div_zeus_like


def test_div_zeus_no_swings_false():
    closes = [float(i) for i in range(20)]
    highs = [c + 0.1 for c in closes]
    lows = [c - 0.1 for c in closes]
    assert div_zeus_like(highs, lows, closes, "long") is False
    assert div_zeus_like(highs, lows, closes, "short") is False


def test_div_zeus_long_ll_rsi_hl(monkeypatch):
    n = 30
    highs = [10.0] * n
    lows = [10.0] * n
    closes = [10.0] * n
    # swing lows at 10 and 20
    for i in range(n):
        lows[i] = 10.0
        highs[i] = 11.0
        closes[i] = 10.5
    lows[8], lows[9], lows[10], lows[11], lows[12] = 9.5, 9.2, 9.0, 9.2, 9.5
    lows[18], lows[19], lows[20], lows[21], lows[22] = 8.8, 8.5, 8.0, 8.5, 8.8

    def fake_rsi(closes_):
        r = [50.0] * len(closes_)
        r[10] = 30.0
        r[20] = 40.0  # HL
        return r

    monkeypatch.setattr(core, "_rsi14", fake_rsi)
    assert div_zeus_like(highs, lows, closes, "long") is True


def test_div_zeus_short_hh_rsi_lh(monkeypatch):
    n = 30
    highs = [10.0] * n
    lows = [9.0] * n
    closes = [9.5] * n
    highs[8], highs[9], highs[10], highs[11], highs[12] = 10.5, 10.8, 11.0, 10.8, 10.5
    highs[18], highs[19], highs[20], highs[21], highs[22] = 11.2, 11.5, 12.0, 11.5, 11.2

    def fake_rsi(closes_):
        r = [50.0] * len(closes_)
        r[10] = 70.0
        r[20] = 60.0  # LH
        return r

    monkeypatch.setattr(core, "_rsi14", fake_rsi)
    assert div_zeus_like(highs, lows, closes, "short") is True


def test_legacy_div_false_zeus_true(monkeypatch):
    """Both swings in second half → legacy mid-split often False; zeus True."""
    n = 40
    highs = [10.0] * n
    lows = [10.0] * n
    closes = [10.0] * n
    # swings at 25 and 32 (both >= mid=20)
    for i in range(n):
        lows[i] = 10.0
        highs[i] = 11.0
        closes[i] = 10.5
    lows[23], lows[24], lows[25], lows[26], lows[27] = 9.5, 9.2, 9.0, 9.2, 9.5
    lows[30], lows[31], lows[32], lows[33], lows[34] = 8.8, 8.5, 8.0, 8.5, 8.8

    def fake_rsi(closes_):
        r = [50.0] * len(closes_)
        r[25] = 30.0
        r[32] = 45.0
        return r

    monkeypatch.setattr(core, "_rsi14", fake_rsi)
    assert div_zeus_like(highs, lows, closes, "long") is True
    # legacy: first half has no distinct lower low pair with +1 RSI rule
    legacy = _rsi_divergence(highs, lows, closes, "long")
    assert legacy is False


def test_evaluate_sets_both_fields(monkeypatch):
    from astra_bot.zeus_wedge_1h_core import evaluate_bars_with_lag

    class _Form:
        form_start = 0
        form_end = 30
        w0 = 0.02
        wE = 0.01
        touches_up = 3
        touches_dn = 3
        kind = "falling"

    monkeypatch.setattr(core, "detect_wedge", lambda *a, **k: _Form())
    monkeypatch.setattr(
        core,
        "scan_breakout_retest",
        lambda *a, **k: {
            "status": "signal",
            "side": "long",
            "entry": 100.0,
            "stop": 98.0,
            "tp1": 103.0,
            "tp2": 106.0,
            "rr_to_tp2": 3.0,
            "breakout_i": 25,
            "retest_i": 26,
            "entry_i": 26,
        },
    )
    monkeypatch.setattr(core, "evaluate_bars", lambda *a, **k: {"status": "no_wedge"})
    monkeypatch.setattr(core, "div_zeus_like", lambda *a, **k: True)
    monkeypatch.setattr(core, "_rsi_divergence", lambda *a, **k: False)
    n = 60
    o = h = l = c = [100.0] * n
    out = evaluate_bars_with_lag(o, h, l, c, lb=48, geom="gold", htf_closes=None)
    assert out.get("status") in ("signal", "htf_gate_blocked")
    assert out.get("div") is False
    assert out.get("div_zeus") is True
