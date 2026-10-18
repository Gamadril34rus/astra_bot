"""K-HTF: 4h trend gate for 1h wedge shadow (paper only)."""
from __future__ import annotations

import json
from pathlib import Path

from astra_bot.zeus_wedge_1h_core import evaluate_bars_with_lag, htf_bias_4h


def test_htf_bias_4h_up_down_neutral():
    # strong uptrend
    up = [float(i) for i in range(1, 40)]
    assert htf_bias_4h(up) == "up"
    down = [float(40 - i) for i in range(40)]
    assert htf_bias_4h(down) == "down"
    flat = [100.0] * 30
    assert htf_bias_4h(flat) == "neutral"
    assert htf_bias_4h(None) == "unknown"
    assert htf_bias_4h([1.0, 2.0]) == "unknown"


def test_htf_gate_blocks_long_at_down(monkeypatch):
    """Force a signal path then apply down bias → htf_gate_blocked."""
    # Build minimal synthetic series via stubbing evaluate internals is hard;
    # call evaluate_bars_with_lag with enough bars and inject side via monkeypatch
    # of scan_breakout_retest / detect_wedge.
    from astra_bot import zeus_wedge_1h_core as core

    class _Form:
        form_start = 0
        form_end = 20
        w0 = 0.02
        wE = 0.01
        touches_up = 3
        touches_dn = 3
        kind = "falling"

    def fake_detect(*a, **k):
        return _Form()

    def fake_scan(*a, **k):
        return {
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
        }

    monkeypatch.setattr(core, "detect_wedge", fake_detect)
    monkeypatch.setattr(core, "scan_breakout_retest", fake_scan)
    monkeypatch.setattr(core, "evaluate_bars", lambda *a, **k: {"status": "no_wedge"})

    n = 60
    o = h = l = c = [100.0] * n
    down = [float(50 - i * 0.5) for i in range(40)]
    out = core.evaluate_bars_with_lag(o, h, l, c, lb=48, geom="gold", htf_closes=down)
    assert out["status"] == "htf_gate_blocked"
    assert out["htf_bias"] == "down"
    assert out["side"] == "long"


def test_htf_gate_blocks_short_at_up(monkeypatch):
    from astra_bot import zeus_wedge_1h_core as core

    class _Form:
        form_start = 0
        form_end = 20
        w0 = 0.02
        wE = 0.01
        touches_up = 3
        touches_dn = 3
        kind = "rising"

    monkeypatch.setattr(core, "detect_wedge", lambda *a, **k: _Form())
    monkeypatch.setattr(
        core,
        "scan_breakout_retest",
        lambda *a, **k: {
            "status": "signal",
            "side": "short",
            "entry": 100.0,
            "stop": 102.0,
            "tp1": 97.0,
            "tp2": 94.0,
            "rr_to_tp2": 3.0,
            "breakout_i": 25,
            "retest_i": 26,
            "entry_i": 26,
        },
    )
    monkeypatch.setattr(core, "evaluate_bars", lambda *a, **k: {"status": "no_wedge"})
    n = 60
    o = h = l = c = [100.0] * n
    up = [float(i) for i in range(1, 40)]
    out = core.evaluate_bars_with_lag(o, h, l, c, lb=48, geom="gold", htf_closes=up)
    assert out["status"] == "htf_gate_blocked"
    assert out["htf_bias"] == "up"


def test_htf_gate_passes_neutral_and_unknown(monkeypatch):
    from astra_bot import zeus_wedge_1h_core as core

    class _Form:
        form_start = 0
        form_end = 20
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
    n = 60
    o = h = l = c = [100.0] * n
    out_n = core.evaluate_bars_with_lag(o, h, l, c, lb=48, htf_closes=[100.0] * 30)
    assert out_n["status"] == "signal"
    assert out_n["htf_bias"] == "neutral"
    out_u = core.evaluate_bars_with_lag(o, h, l, c, lb=48, htf_closes=None)
    assert out_u["status"] == "signal"
    assert out_u["htf_bias"] == "unknown"


def test_htf_blocked_row_written_to_jsonl(tmp_path, monkeypatch):
    from scripts import zeus_wedge_1h_shadow as sh
    from astra_bot import zeus_wedge_1h_core as core

    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()

    class _Form:
        form_start = 0
        form_end = 20
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

    # build fake bars objects with close attr
    class B:
        def __init__(self, i):
            self.open = 100.0
            self.high = 101.0
            self.low = 99.0
            self.close = 100.0
            self.volume = 1.0
            self.timestamp = 1_700_000_000_000 + i * 3600_000

    bars = [B(i) for i in range(80)]
    down = [float(50 - i * 0.5) for i in range(40)]
    sh.log_wedge_1h_shadow(
        symbol="TEST-USDT",
        bars_1h=bars,
        htf_closes=down,
        lbs=[48],
    )
    path = tmp_path / "models" / "zeus_wedge_1h_signals.jsonl"
    assert path.exists()
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    blocked = [r for r in rows if r.get("status") == "htf_gate_blocked"]
    assert blocked, rows[:3]
    assert blocked[0]["htf_bias"] == "down"
    assert blocked[0]["side"] == "long"
