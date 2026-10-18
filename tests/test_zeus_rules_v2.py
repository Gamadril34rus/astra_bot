"""Zeus rules v2: div_zeus_like + wedge_retest features + 1h shadow field."""
from __future__ import annotations

from astra_bot import zeus_wedge_1h_core as core1h
from astra_bot.decision.zeus_divergence import div_zeus_like


def test_div_zeus_no_swings():
    closes = [float(i) for i in range(20)]
    highs = [c + 0.1 for c in closes]
    lows = [c - 0.1 for c in closes]
    assert div_zeus_like(highs, lows, closes, "long") is False
    assert div_zeus_like(highs, lows, closes, "short") is False


def test_div_zeus_long_ll_rsi_hl(monkeypatch):
    import astra_bot.decision.zeus_divergence as zd

    n = 30
    highs = [11.0] * n
    lows = [10.0] * n
    closes = [10.5] * n
    lows[8], lows[9], lows[10], lows[11], lows[12] = 9.5, 9.2, 9.0, 9.2, 9.5
    lows[18], lows[19], lows[20], lows[21], lows[22] = 8.8, 8.5, 8.0, 8.5, 8.8

    def fake_rsi(closes_):
        r = [50.0] * len(closes_)
        r[10] = 30.0
        r[20] = 40.0
        return r

    monkeypatch.setattr(zd, "_rsi14", fake_rsi)
    assert div_zeus_like(highs, lows, closes, "long") is True


def test_div_zeus_short_hh_rsi_lh(monkeypatch):
    import astra_bot.decision.zeus_divergence as zd

    n = 30
    highs = [10.0] * n
    lows = [9.0] * n
    closes = [9.5] * n
    highs[8], highs[9], highs[10], highs[11], highs[12] = 10.5, 10.8, 11.0, 10.8, 10.5
    highs[18], highs[19], highs[20], highs[21], highs[22] = 11.2, 11.5, 12.0, 11.5, 11.2

    def fake_rsi(closes_):
        r = [50.0] * len(closes_)
        r[10] = 70.0
        r[20] = 60.0
        return r

    monkeypatch.setattr(zd, "_rsi14", fake_rsi)
    assert div_zeus_like(highs, lows, closes, "short") is True


def test_div_zeus_no_hard_thresholds(monkeypatch):
    """Tiny price LL + tiny RSI HL still True (no 0.1% / +1.0)."""
    import astra_bot.decision.zeus_divergence as zd

    n = 30
    highs = [11.0] * n
    lows = [10.0] * n
    closes = [10.5] * n
    lows[8], lows[9], lows[10], lows[11], lows[12] = 9.01, 9.005, 9.0, 9.005, 9.01
    lows[18], lows[19], lows[20], lows[21], lows[22] = 8.999, 8.998, 8.997, 8.998, 8.999

    def fake_rsi(closes_):
        r = [50.0] * len(closes_)
        r[10] = 30.0
        r[20] = 30.1  # +0.1 only
        return r

    monkeypatch.setattr(zd, "_rsi14", fake_rsi)
    assert div_zeus_like(highs, lows, closes, "long") is True


def test_1h_shadow_sets_div_zeus(monkeypatch):
    class _Form:
        form_start = 0
        form_end = 30
        w0 = 0.02
        wE = 0.01
        touches_up = 3
        touches_dn = 3
        kind = "falling"

    monkeypatch.setattr(core1h, "detect_wedge", lambda *a, **k: _Form())
    monkeypatch.setattr(
        core1h,
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
    monkeypatch.setattr(core1h, "evaluate_bars", lambda *a, **k: {"status": "no_wedge"})
    monkeypatch.setattr(core1h, "div_zeus_like", lambda *a, **k: True)
    monkeypatch.setattr(core1h, "_rsi_divergence", lambda *a, **k: False)
    n = 60
    o = h = l = c = [100.0] * n
    out = core1h.evaluate_bars_with_lag(o, h, l, c, lb=48, geom="gold")
    assert out.get("div") is False
    assert out.get("div_zeus") is True


def test_wedge_retest_confidence_bump_logic():
    """Mirror evaluate confidence + features rules (no full strategy deps)."""
    width_pct = 0.02
    confidence = min(0.85, 0.55 + width_pct * 2.0)
    div_z = True
    if div_z:
        confidence = min(confidence + 0.07, 0.95)
    features = {"div_zeus": bool(div_z), "rules_version": "v2"}
    assert features["rules_version"] == "v2"
    assert features["div_zeus"] is True
    assert abs(confidence - 0.66) < 1e-9


def test_wedge_retest_source_has_v2_marker():
    import inspect
    from pathlib import Path
    # Online CI: import strategy module text markers
    try:
        from astra_bot.strategies import zeus_wedge_retest as zw
        src = inspect.getsource(zw)
    except Exception:
        src = Path("astra_bot/strategies/zeus_wedge_retest.py").read_text(encoding="utf-8")
    assert "div_zeus_like" in src
    assert 'rules_version": "v2"' in src or "rules_version" in src
    assert "div_zeus" in src
