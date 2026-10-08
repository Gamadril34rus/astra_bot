"""Synthetic unit test for zeus_wedge_retest_1h detector stages."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from astra_bot.zeus_wedge_1h_core import (
    detect_wedge,
    evaluate_bars,
    evaluate_bars_with_lag,
    scan_breakout_retest,
    _classify_kind,
)


def test_gold_classify_matches_brief():
    assert _classify_kind(-0.02, -0.05, 0.05, 0.04, "gold") == "falling"
    assert _classify_kind(0.05, 0.02, 0.05, 0.04, "gold") == "rising"
    assert _classify_kind(-0.05, -0.02, 0.05, 0.04, "gold") is None


def test_narrow_classify_classic():
    assert _classify_kind(-0.05, -0.02, 0.05, 0.04, "narrow") == "falling"
    assert _classify_kind(0.05, 0.02, 0.05, 0.04, "narrow") == "rising"


def _narrow_series(lb: int = 48):
    """Formation on first 48 bars, breakout/retest/entry after. No form_end mutation for prod path."""
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
    form = detect_wedge(highs, lows, closes, lb=lb, geom="narrow")
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


def test_detect_narrow_falling_wedge():
    o, h, l, c, form = _narrow_series()
    assert form.kind == "falling"
    assert form.geom == "narrow"
    assert form.touches_up >= 2 and form.touches_dn >= 2


def test_breakout_retest_entry():
    o, h, l, c, form = _narrow_series()
    br = scan_breakout_retest(h, l, o, c, form)
    assert br is not None
    assert br.get("status") == "signal", br
    assert br["side"] == "long"
    assert br["entry"] > 0 and br["stop"] < br["entry"]
    assert br["tp1"] > br["entry"]


def test_evaluate_bars_signal_production():
    """Blocker 1: production lag-scan without manual form_end must yield signal."""
    o, h, l, c, _ = _narrow_series()
    naive = evaluate_bars(o, h, l, c, lb=48, geom="narrow")
    assert naive.get("status") == "no_breakout", naive
    prod = evaluate_bars_with_lag(o, h, l, c, lb=48, geom="narrow")
    assert prod.get("status") == "signal", prod
    assert prod["side"] == "long"
    assert prod.get("entry") is not None
    assert prod.get("entry_i") is not None


def test_shadow_log_row(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import zeus_wedge_1h_shadow as sh

    monkeypatch.setattr(sh, "SHADOW_PATH", tmp_path / "signals.jsonl")
    monkeypatch.setattr(sh, "STATE_PATH", tmp_path / "state.json")
    o, h, l, c, form = _narrow_series()
    base_ts = 1_700_000_000
    pad = [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 500.0,
            "open_time": base_ts + i * 3600} for i in range(40)]
    bars = pad + [
        {"open": o[i], "high": h[i], "low": l[i], "close": c[i], "volume": 1000.0,
         "open_time": base_ts + (40 + i) * 3600}
        for i in range(len(o))
    ]
    bars.append({"open": c[-1], "high": c[-1], "low": c[-1], "close": c[-1], "volume": 1,
                 "open_time": base_ts + (40 + len(o)) * 3600})
    sh.log_wedge_1h_shadow(symbol="BTC-USDT", bars_1h=bars, lb=48)
    assert sh.SHADOW_PATH.exists()
    lines = [ln for ln in sh.SHADOW_PATH.read_text().splitlines() if ln.strip()]
    assert len(lines) >= 1
    statuses = {json.loads(ln)["status"] for ln in lines}
    assert "signal" in statuses, statuses
    geoms = {json.loads(ln).get("geom") for ln in lines}
    assert "gold" in geoms and "narrow" in geoms


def test_shadow_closes_on_slid_window(tmp_path, monkeypatch):
    """Blocker 2: after window slides, stop hit must produce closed row + drop open."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import zeus_wedge_1h_shadow as sh

    monkeypatch.setattr(sh, "SHADOW_PATH", tmp_path / "signals.jsonl")
    monkeypatch.setattr(sh, "STATE_PATH", tmp_path / "state.json")

    o, h, l, c, form = _narrow_series()
    base_ts = 1_700_000_000

    def make_bars(o, h, l, c, base, extra_bars=None):
        pad = [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 500.0,
                "open_time": base + i * 3600} for i in range(40)]
        bars = pad + [
            {"open": o[i], "high": h[i], "low": l[i], "close": c[i], "volume": 1000.0,
             "open_time": base + (40 + i) * 3600}
            for i in range(len(o))
        ]
        if extra_bars:
            bars.extend(extra_bars)
        last = bars[-1]
        bars.append({
            "open": last["close"], "high": last["close"], "low": last["close"],
            "close": last["close"], "volume": 1,
            "open_time": last["open_time"] + 3600,
        })
        return bars

    bars0 = make_bars(o, h, l, c, base_ts)
    sh.log_wedge_1h_shadow(symbol="BTC-USDT", bars_1h=bars0, lb=48)
    st = sh._load_state()
    assert st["open"], "expected open shadow position after signal"
    key, pos = next(iter(st["open"].items()))
    entry = float(pos["entry"])
    stop = float(pos["stop"])
    entry_ts = pos["entry_ts"]
    assert pos["side"] == "long"
    assert stop < entry

    pierce = {
        "open": entry, "high": entry * 1.001, "low": stop * 0.99, "close": stop * 0.995,
        "volume": 2000.0, "open_time": int(entry_ts / 1000) + 7200,
    }
    bars1 = make_bars(o, h, l, c, base_ts + 3600, extra_bars=[pierce])
    bars1[-2]["open_time"] = int(entry_ts / 1000) + 7200

    sh.update_open_shadows(symbol="BTC-USDT", bars_1h=bars1)
    st2 = sh._load_state()
    assert key not in st2["open"], "position must be removed after stop"
    lines = [json.loads(ln) for ln in sh.SHADOW_PATH.read_text().splitlines() if ln.strip()]
    closed = [r for r in lines if r.get("status") == "closed"]
    assert closed, "expected closed row in jsonl"
    assert closed[-1]["result_r"] < 0
    assert closed[-1]["exit_reason"] in ("stop", "be_stop")


def test_dedup_collapse_on_slid_window(tmp_path, monkeypatch):
    """Problem A: sliding window must not re-open same episode (key = symbol|geom|side)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import zeus_wedge_1h_shadow as sh

    monkeypatch.setattr(sh, "SHADOW_PATH", tmp_path / "signals.jsonl")
    monkeypatch.setattr(sh, "STATE_PATH", tmp_path / "state.json")

    o, h, l, c, form = _narrow_series()
    base_ts = 1_700_000_000
    pad = [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 500.0,
            "open_time": base_ts + i * 3600} for i in range(40)]
    bars = pad + [
        {"open": o[i], "high": h[i], "low": l[i], "close": c[i], "volume": 1000.0,
         "open_time": base_ts + (40 + i) * 3600}
        for i in range(len(o))
    ]
    last = bars[-1]
    bars.append({"open": last["close"], "high": last["close"], "low": last["close"],
                 "close": last["close"], "volume": 1, "open_time": last["open_time"] + 3600})

    sh.log_wedge_1h_shadow(symbol="BTC-USDT", bars_1h=bars, lb=48)
    lines0 = [json.loads(ln) for ln in sh.SHADOW_PATH.read_text().splitlines() if ln.strip()]
    signals0 = [r for r in lines0 if r.get("status") == "signal"]
    assert signals0, "expected initial signal"
    n_sig0 = len(signals0)
    st0 = sh._load_state()
    n_open0 = len(st0["open"])
    assert n_open0 >= 1

    # Hour H+1: APPEND one new bar; keep STABLE timestamps on old bars
    closed = bars[:-1]
    cont_ts = closed[-1]["open_time"] + 3600
    closed.append({
        "open": closed[-1]["close"], "high": closed[-1]["close"] * 1.002,
        "low": closed[-1]["close"] * 0.998, "close": closed[-1]["close"] * 1.001,
        "volume": 1100.0, "open_time": cont_ts,
    })
    closed.append({
        "open": closed[-1]["close"], "high": closed[-1]["close"], "low": closed[-1]["close"],
        "close": closed[-1]["close"], "volume": 1, "open_time": cont_ts + 3600,
    })
    sh.log_wedge_1h_shadow(symbol="BTC-USDT", bars_1h=closed, lb=48)
    lines1 = [json.loads(ln) for ln in sh.SHADOW_PATH.read_text().splitlines() if ln.strip()]
    signals1 = [r for r in lines1 if r.get("status") == "signal"]
    assert len(signals1) == n_sig0, (n_sig0, len(signals1), signals1)
    st1 = sh._load_state()
    assert len(st1["open"]) == n_open0


def test_lag_scan_not_blocked_by_stage(tmp_path, monkeypatch):
    """Problem B: lag=0 rejected_width must not prevent lag-scan signal."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import zeus_wedge_1h_shadow as sh

    monkeypatch.setattr(sh, "SHADOW_PATH", tmp_path / "signals.jsonl")
    monkeypatch.setattr(sh, "STATE_PATH", tmp_path / "state.json")

    o, h, l, c, form = _narrow_series()
    h = list(h); l = list(l); o = list(o); c = list(c)
    h[-1] = max(h) * 1.5
    l[-1] = min(l) * 0.5
    c[-1] = (h[-1] + l[-1]) / 2
    o[-1] = c[-1]

    stage = evaluate_bars(o, h, l, c, lb=48, geom="narrow")
    assert stage.get("status") in ("rejected_width", "no_wedge", "rejected_touches", "no_breakout"), stage

    prod = evaluate_bars_with_lag(o, h, l, c, lb=48, geom="narrow")
    assert prod.get("status") == "signal", prod

    base_ts = 1_700_000_000
    pad = [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 500.0,
            "open_time": base_ts + i * 3600} for i in range(40)]
    bars = pad + [
        {"open": o[i], "high": h[i], "low": l[i], "close": c[i], "volume": 1000.0,
         "open_time": base_ts + (40 + i) * 3600}
        for i in range(len(o))
    ]
    bars.append({"open": c[-1], "high": c[-1], "low": c[-1], "close": c[-1], "volume": 1,
                 "open_time": base_ts + (40 + len(o)) * 3600})
    sh.log_wedge_1h_shadow(symbol="ETH-USDT", bars_1h=bars, lb=48)
    lines = [json.loads(ln) for ln in sh.SHADOW_PATH.read_text().splitlines() if ln.strip()]
    assert any(r.get("status") == "signal" for r in lines), lines
