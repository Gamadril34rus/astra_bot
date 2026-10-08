"""Synthetic unit test for zeus_wedge_retest_1h detector stages."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from astra_bot.zeus_wedge_1h_core import detect_wedge, scan_breakout_retest


def _falling_wedge_series(lb: int = 48):
    opens, highs, lows, closes = [], [], [], []
    for i in range(lb):
        up = 102.0 - 0.05 * i
        dn = 98.0 - 0.02 * i
        if i % 2 == 0:
            hi, lo = up, dn + 0.3 * (up - dn)
        else:
            hi, lo = up - 0.3 * (up - dn), dn
        mid = 0.5 * (hi + lo)
        opens.append(mid); highs.append(hi); lows.append(lo); closes.append(mid)
    form = detect_wedge(highs, lows, closes, lb=lb)
    assert form is not None
    form.form_start = 0
    form.form_end = lb
    j = lb
    up = form.u0 + form.su * j
    opens.append(up * 1.01); highs.append(up * 1.03); lows.append(up * 0.999); closes.append(up * 1.02)
    j = lb + 1
    line = form.u0 + form.su * j
    opens.append(line * 1.001); highs.append(line * 1.01); lows.append(line * 0.999); closes.append(line * 1.001)
    entry = line * 1.002
    opens.append(entry); highs.append(entry * 1.01); lows.append(entry * 0.99); closes.append(entry * 1.001)
    return opens, highs, lows, closes, form


def test_detect_falling_wedge():
    o, h, l, c, form = _falling_wedge_series()
    assert form.kind == "falling"
    assert form.side_break == "long"
    assert form.touches_up >= 2 and form.touches_dn >= 2
    assert 0.005 <= form.w0 <= 0.15


def test_breakout_retest_entry():
    o, h, l, c, form = _falling_wedge_series()
    br = scan_breakout_retest(h, l, o, c, form)
    assert br is not None
    assert br.get("status") == "signal", br
    assert br["side"] == "long"
    assert br["entry"] > 0
    assert br["stop"] < br["entry"]
    assert br["tp1"] > br["entry"]
    assert br["tp2"] > br["entry"]


def test_shadow_log_row(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import zeus_wedge_1h_shadow as sh

    monkeypatch.setattr(sh, "SHADOW_PATH", tmp_path / "signals.jsonl")
    monkeypatch.setattr(sh, "STATE_PATH", tmp_path / "state.json")
    o, h, l, c, form = _falling_wedge_series()
    pad = [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 500.0}] * 40
    bars = pad + [
        {"open": o[i], "high": h[i], "low": l[i], "close": c[i], "volume": 1000.0}
        for i in range(len(o))
    ]
    bars.append({"open": c[-1], "high": c[-1], "low": c[-1], "close": c[-1], "volume": 1})
    sh.log_wedge_1h_shadow(symbol="BTC-USDT", bars_1h=bars, lb=48)
    assert sh.SHADOW_PATH.exists()
    lines = [ln for ln in sh.SHADOW_PATH.read_text().splitlines() if ln.strip()]
    assert lines
    rec = json.loads(lines[-1])
    assert rec["symbol"] == "BTC-USDT"
    assert "signal_id" in rec and "status" in rec
