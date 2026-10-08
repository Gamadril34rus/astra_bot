"""Pure 1h wedge detector (no BaseStrategy / no heavy deps).

Primary geometry (\"gold\") matches the research brief verbatim:
  falling: su<0 and sl<0 and sl<su and wE<0.95*w0
  rising:  su>0 and sl>0 and su>sl and wE<0.95*w0
where w0=(maxH-minL)/close[first], wE=(uE-lE)/close[last].

Optional second stream \"narrow\" (classic converging rails) tagged via geom.
Production signals use evaluate_bars_with_lag (formation ends k bars ago).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

COST_PER_SIDE = 0.0015
TOUCH_TOL = 0.0035
RETEST_TOL = 0.003
CANCEL_TOL = 0.003
BREAKOUT_MAX_BARS = 20
RETEST_MAX_BARS = 8
W0_MIN, W0_MAX = 0.005, 0.15
STOP_BUF_LONG = 0.998
STOP_BUF_SHORT = 1.002


def _linreg(ys: Sequence[float]) -> tuple[float, float]:
    n = len(ys)
    if n < 2:
        return (float(ys[0]) if ys else 0.0), 0.0
    sx = n * (n - 1) / 2.0
    sy = sum(ys)
    sxx = (n - 1) * n * (2 * n - 1) / 6.0
    sxy = sum(i * y for i, y in enumerate(ys))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-18:
        return sy / n, 0.0
    b = (n * sxy - sx * sy) / denom
    a = (sy - b * sx) / n
    return a, b


def _rsi14(closes: Sequence[float]) -> list[float]:
    n = len(closes)
    out = [50.0] * n
    if n < 15:
        return out
    gains = [0.0]
    losses = [0.0]
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[1:15]) / 14.0
    avg_l = sum(losses[1:15]) / 14.0
    for i in range(14, n):
        if i > 14:
            avg_g = (avg_g * 13 + gains[i]) / 14.0
            avg_l = (avg_l * 13 + losses[i]) / 14.0
        if avg_l < 1e-12:
            out[i] = 100.0
        else:
            rs = avg_g / avg_l
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def _rsi_divergence(highs, lows, closes, side: str) -> bool:
    n = len(closes)
    if n < 16:
        return False
    rsi = _rsi14(closes)
    mid = n // 2
    if side == "long":
        i1 = min(range(mid), key=lambda i: lows[i])
        i2 = min(range(mid, n), key=lambda i: lows[i])
        return lows[i2] <= lows[i1] * 0.999 and rsi[i2] > rsi[i1] + 1.0
    i1 = max(range(mid), key=lambda i: highs[i])
    i2 = max(range(mid, n), key=lambda i: highs[i])
    return highs[i2] >= highs[i1] * 1.001 and rsi[i2] < rsi[i1] - 1.0


def wilder_adx(highs, lows, closes, period: int = 14) -> float | None:
    n = len(closes)
    if n < period * 2 + 1:
        return None
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, n):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        trs.append(tr)
        plus_dm.append(up if up > dn and up > 0 else 0.0)
        minus_dm.append(dn if dn > up and dn > 0 else 0.0)
    atr = sum(trs[:period]) / period
    p_dm = sum(plus_dm[:period]) / period
    m_dm = sum(minus_dm[:period]) / period
    dx_list: list[float] = []
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
        p_dm = (p_dm * (period - 1) + plus_dm[i]) / period
        m_dm = (m_dm * (period - 1) + minus_dm[i]) / period
        p_di = 100.0 * p_dm / atr if atr else 0.0
        m_di = 100.0 * m_dm / atr if atr else 0.0
        s = p_di + m_di
        dx_list.append(100.0 * abs(p_di - m_di) / s if s else 0.0)
    if len(dx_list) < period:
        return None
    adx = sum(dx_list[:period]) / period
    for dx in dx_list[period:]:
        adx = (adx * (period - 1) + dx) / period
    return adx


def _classify_kind(su: float, sl: float, w0: float, wE: float, geom: str) -> str | None:
    if geom == "gold":
        if su < 0 and sl < 0 and sl < su and wE < 0.95 * w0:
            return "falling"
        if su > 0 and sl > 0 and su > sl and wE < 0.95 * w0:
            return "rising"
    else:
        if su < 0 and sl < 0 and su < sl and wE < 0.95 * w0:
            return "falling"
        if su > 0 and sl > 0 and su > sl and wE < 0.95 * w0:
            return "rising"
    return None


@dataclass
class WedgeFormation:
    kind: str
    side_break: str
    u0: float
    su: float
    l0: float
    sl: float
    w0: float
    wE: float
    touches_up: int
    touches_dn: int
    form_start: int
    form_end: int
    form_high: float
    form_low: float
    geom: str = "gold"


def detect_wedge(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
    *, lb: int = 48, touch_tol: float = TOUCH_TOL, w0_min: float = W0_MIN,
    w0_max: float = W0_MAX, geom: str = "gold",
) -> WedgeFormation | None:
    n = len(highs)
    if n < lb or len(lows) < lb or len(closes) < lb:
        return None
    start = n - lb
    h = list(highs[start:])
    l = list(lows[start:])
    c_first = float(closes[start])
    c_last = float(closes[start + lb - 1])
    if c_first <= 0 or c_last <= 0:
        return None
    u0, su = _linreg(h)
    l0, sl = _linreg(l)
    uE = u0 + su * (lb - 1)
    lE = l0 + sl * (lb - 1)
    w0 = (max(h) - min(l)) / c_first
    wE = (uE - lE) / c_last
    if not (w0_min <= w0 <= w0_max):
        return None
    if (u0 - l0) <= 0:
        return None
    kind = _classify_kind(su, sl, w0, wE, geom)
    if kind is None:
        return None
    touches_up = touches_dn = 0
    for i in range(lb):
        up_line = u0 + su * i
        dn_line = l0 + sl * i
        if h[i] >= up_line * (1.0 - touch_tol):
            touches_up += 1
        if l[i] <= dn_line * (1.0 + touch_tol):
            touches_dn += 1
    if touches_up < 2 or touches_dn < 2:
        return None
    side = "long" if kind == "falling" else "short"
    return WedgeFormation(
        kind=kind, side_break=side, u0=u0, su=su, l0=l0, sl=sl,
        w0=w0, wE=wE, touches_up=touches_up, touches_dn=touches_dn,
        form_start=start, form_end=n, form_high=max(h), form_low=min(l), geom=geom,
    )


def scan_breakout_retest(
    highs, lows, opens, closes, form: WedgeFormation,
    *, breakout_max: int = BREAKOUT_MAX_BARS, retest_max: int = RETEST_MAX_BARS,
) -> dict[str, Any] | None:
    n = len(closes)
    form_end = form.form_end
    if form_end >= n:
        return None
    side = form.side_break
    breakout_i = None
    for j in range(form_end, min(n, form_end + breakout_max)):
        t = j - form.form_start
        up = form.u0 + form.su * t
        dn = form.l0 + form.sl * t
        if side == "long" and closes[j] > up:
            breakout_i = j
            break
        if side == "short" and closes[j] < dn:
            breakout_i = j
            break
    if breakout_i is None:
        return {"status": "no_breakout"}
    retest_i = None
    for j in range(breakout_i + 1, min(n, breakout_i + 1 + retest_max)):
        t = j - form.form_start
        up = form.u0 + form.su * t
        dn = form.l0 + form.sl * t
        line = up if side == "long" else dn
        if side == "long":
            if closes[j] < line * (1.0 - CANCEL_TOL):
                return {"status": "no_retest", "breakout_i": breakout_i}
            if lows[j] <= line * (1.0 + RETEST_TOL) and closes[j] >= line:
                retest_i = j
                break
        else:
            if closes[j] > line * (1.0 + CANCEL_TOL):
                return {"status": "no_retest", "breakout_i": breakout_i}
            if highs[j] >= line * (1.0 - RETEST_TOL) and closes[j] <= line:
                retest_i = j
                break
    if retest_i is None:
        return {"status": "no_retest", "breakout_i": breakout_i}
    entry_i = retest_i + 1
    if entry_i >= n:
        return {"status": "signal_pending_entry", "breakout_i": breakout_i, "retest_i": retest_i}
    entry = float(opens[entry_i])
    seg_lows = list(lows[form.form_start : retest_i + 1])
    seg_highs = list(highs[form.form_start : retest_i + 1])
    if side == "long":
        stop = min(seg_lows) * STOP_BUF_LONG
        t_bo = breakout_i - form.form_start
        tp1 = 0.5 * (form.u0 + form.su * t_bo + form.l0 + form.sl * t_bo)
        risk = entry - stop
        if risk <= 0:
            return {"status": "rejected_stop"}
        if tp1 <= entry:
            tp1 = entry + risk
        tp2 = form.form_high
    else:
        stop = max(seg_highs) * STOP_BUF_SHORT
        t_bo = breakout_i - form.form_start
        tp1 = 0.5 * (form.u0 + form.su * t_bo + form.l0 + form.sl * t_bo)
        risk = stop - entry
        if risk <= 0:
            return {"status": "rejected_stop"}
        if tp1 >= entry:
            tp1 = entry - risk
        tp2 = form.form_low
    rr = abs(tp2 - entry) / risk if risk > 0 else 0.0
    return {
        "status": "signal", "side": side, "breakout_i": breakout_i, "retest_i": retest_i,
        "entry_i": entry_i, "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2,
        "rr_to_tp2": rr, "risk": risk,
    }


def evaluate_bars(opens, highs, lows, closes, *, lb: int = 48, geom: str = "gold") -> dict[str, Any]:
    form = detect_wedge(highs, lows, closes, lb=lb, geom=geom)
    if form is None:
        n = len(highs)
        if n < lb:
            return {"status": "insufficient_bars", "geom": geom}
        start = n - lb
        h = list(highs[start:])
        l = list(lows[start:])
        c0 = float(closes[start]) or 1.0
        c1 = float(closes[start + lb - 1]) or 1.0
        w0 = (max(h) - min(l)) / c0
        if not (W0_MIN <= w0 <= W0_MAX):
            return {"status": "rejected_width", "w0": w0, "geom": geom}
        u0, su = _linreg(h)
        l0, sl = _linreg(l)
        uE = u0 + su * (lb - 1)
        lE = l0 + sl * (lb - 1)
        wE = (uE - lE) / c1
        if _classify_kind(su, sl, w0, wE, geom) is None:
            return {"status": "no_wedge", "w0": w0, "wE": wE, "geom": geom}
        return {"status": "rejected_touches", "w0": w0, "wE": wE, "geom": geom}
    br = scan_breakout_retest(highs, lows, opens, closes, form)
    if br is None:
        return {"status": "no_breakout", "form": form, "geom": geom}
    out: dict[str, Any] = {
        "status": br.get("status"), "form": form, "w0": form.w0, "wE": form.wE,
        "touches_up": form.touches_up, "touches_dn": form.touches_dn,
        "kind": form.kind, "lb": lb, "geom": geom,
    }
    out.update({k: v for k, v in br.items() if k != "status"})
    if br.get("status") == "signal":
        side = br["side"]
        out["div"] = _rsi_divergence(
            highs[form.form_start:form.form_end], lows[form.form_start:form.form_end],
            closes[form.form_start:form.form_end], side,
        )
        out["side"] = side
    return out


def evaluate_bars_with_lag(
    opens: Sequence[float], highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
    *, lb: int = 48, geom: str = "gold", max_lag: int | None = None,
) -> dict[str, Any]:
    """Fit formation ending `lag` bars ago; scan breakout/retest on the FULL series.

    Production path: detect_wedge on closes[:n-lag] so form_end < n.
    First lag with signal or signal_pending_entry wins.
    Stage telemetry (rejected_*) is lag=0 only; does NOT block lag-scan
    except insufficient_bars.
    """
    n = len(closes)
    if max_lag is None:
        max_lag = BREAKOUT_MAX_BARS + RETEST_MAX_BARS + 1
    stage = evaluate_bars(opens, highs, lows, closes, lb=lb, geom=geom)
    if stage.get("status") == "insufficient_bars":
        return stage
    best: dict[str, Any] | None = None
    for lag in range(1, max_lag + 1):
        end = n - lag
        if end < lb:
            break
        form = detect_wedge(highs[:end], lows[:end], closes[:end], lb=lb, geom=geom)
        if form is None:
            continue
        br = scan_breakout_retest(highs, lows, opens, closes, form)
        if br is None:
            continue
        st = br.get("status")
        if st in ("signal", "signal_pending_entry"):
            out: dict[str, Any] = {
                "status": st, "form": form, "w0": form.w0, "wE": form.wE,
                "touches_up": form.touches_up, "touches_dn": form.touches_dn,
                "kind": form.kind, "lb": lb, "geom": geom, "lag": lag,
            }
            out.update({k: v for k, v in br.items() if k != "status"})
            if st == "signal":
                side = br["side"]
                out["div"] = _rsi_divergence(
                    highs[form.form_start:form.form_end], lows[form.form_start:form.form_end],
                    closes[form.form_start:form.form_end], side,
                )
                out["side"] = side
            return out
        if best is None and st in ("no_breakout", "no_retest"):
            best = {
                "status": st, "form": form, "w0": form.w0, "wE": form.wE,
                "touches_up": form.touches_up, "touches_dn": form.touches_dn,
                "kind": form.kind, "lb": lb, "geom": geom, "lag": lag,
            }
            best.update({k: v for k, v in br.items() if k != "status"})
    return best if best is not None else stage
