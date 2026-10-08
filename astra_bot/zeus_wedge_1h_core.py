"""Pure 1h wedge detector (no BaseStrategy / no heavy deps)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

COST_PER_SIDE = 0.0015  # model cost per side
TOUCH_TOL = 0.0035
RETEST_TOL = 0.003
CANCEL_TOL = 0.003
BREAKOUT_MAX_BARS = 20
RETEST_MAX_BARS = 8
W0_MIN, W0_MAX = 0.005, 0.15
STOP_BUF_LONG = 0.998
STOP_BUF_SHORT = 1.002


def _linreg(ys: Sequence[float]) -> tuple[float, float]:
    """OLS: y = a + b*x, x=0..n-1. Returns (a, b)."""
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


def _rsi_divergence(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    side: str,
) -> bool:
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


@dataclass
class ZeusWedge1hCoreConfig:
    name: str = "zeus_wedge_retest_1h"
    enabled: bool = False
    preferred_timeframe: str = "1h"
    lookback: int = 48
    min_touches: int = 2
    breakout_max_bars: int = BREAKOUT_MAX_BARS
    retest_max_bars: int = RETEST_MAX_BARS
    w0_min: float = W0_MIN
    w0_max: float = W0_MAX
    touch_tol: float = TOUCH_TOL
    cost_per_side: float = COST_PER_SIDE


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


def detect_wedge(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    lb: int = 48,
    touch_tol: float = TOUCH_TOL,
    w0_min: float = W0_MIN,
    w0_max: float = W0_MAX,
) -> WedgeFormation | None:
    n = len(highs)
    if n < lb or len(lows) < lb or len(closes) < lb:
        return None
    start = n - lb
    h = list(highs[start:])
    l = list(lows[start:])
    c0 = float(closes[start])
    if c0 <= 0:
        return None
    u0, su = _linreg(h)
    l0, sl = _linreg(l)
    uE = u0 + su * (lb - 1)
    lE = l0 + sl * (lb - 1)
    w0 = (max(h) - min(l)) / c0
    if not (w0_min <= w0 <= w0_max):
        return None
    width0 = u0 - l0
    widthE = uE - lE
    if width0 <= 0:
        return None
    # Falling: both down, upper steeper (su < sl < 0) → width shrinks.
    # Rising: both up, upper steeper (su > sl > 0) → width shrinks.
    kind = None
    if su < 0 and sl < 0 and su < sl and widthE < 0.95 * width0:
        kind = "falling"
    elif su > 0 and sl > 0 and su > sl and widthE < 0.95 * width0:
        kind = "rising"
    if kind is None:
        return None
    touches_up = 0
    touches_dn = 0
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
        kind=kind,
        side_break=side,
        u0=u0,
        su=su,
        l0=l0,
        sl=sl,
        w0=w0,
        wE=(uE - lE) / c0 if c0 else 0.0,
        touches_up=touches_up,
        touches_dn=touches_dn,
        form_start=start,
        form_end=n,
        form_high=max(h),
        form_low=min(l),
    )


def scan_breakout_retest(
    highs: Sequence[float],
    lows: Sequence[float],
    opens: Sequence[float],
    closes: Sequence[float],
    form: WedgeFormation,
    *,
    breakout_max: int = BREAKOUT_MAX_BARS,
    retest_max: int = RETEST_MAX_BARS,
) -> dict[str, Any] | None:
    n = len(closes)
    form_end = form.form_end
    if form_end >= n:
        return None
    side = form.side_break
    breakout_i = None
    break_line = 0.0
    for j in range(form_end, min(n, form_end + breakout_max)):
        t = j - form.form_start
        up = form.u0 + form.su * t
        dn = form.l0 + form.sl * t
        if side == "long" and closes[j] > up:
            breakout_i = j
            break_line = up
            break
        if side == "short" and closes[j] < dn:
            breakout_i = j
            break_line = dn
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
        return {
            "status": "signal_pending_entry",
            "breakout_i": breakout_i,
            "retest_i": retest_i,
        }
    entry = float(opens[entry_i])
    seg_lows = list(lows[form.form_start : retest_i + 1])
    seg_highs = list(highs[form.form_start : retest_i + 1])
    if side == "long":
        stop = min(seg_lows) * STOP_BUF_LONG
        t_bo = breakout_i - form.form_start
        up_bo = form.u0 + form.su * t_bo
        dn_bo = form.l0 + form.sl * t_bo
        tp1 = 0.5 * (up_bo + dn_bo)
        risk = entry - stop
        if risk <= 0:
            return {"status": "rejected_stop"}
        if tp1 <= entry:
            tp1 = entry + risk
        tp2 = form.form_high
    else:
        stop = max(seg_highs) * STOP_BUF_SHORT
        t_bo = breakout_i - form.form_start
        up_bo = form.u0 + form.su * t_bo
        dn_bo = form.l0 + form.sl * t_bo
        tp1 = 0.5 * (up_bo + dn_bo)
        risk = stop - entry
        if risk <= 0:
            return {"status": "rejected_stop"}
        if tp1 >= entry:
            tp1 = entry - risk
        tp2 = form.form_low
    rr = abs(tp2 - entry) / risk if risk > 0 else 0.0
    return {
        "status": "signal",
        "side": side,
        "breakout_i": breakout_i,
        "retest_i": retest_i,
        "entry_i": entry_i,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "rr_to_tp2": rr,
        "risk": risk,
    }


def evaluate_bars(
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    lb: int = 48,
) -> dict[str, Any]:
    form = detect_wedge(highs, lows, closes, lb=lb)
    if form is None:
        n = len(highs)
        if n < lb:
            return {"status": "insufficient_bars"}
        start = n - lb
        h = list(highs[start:])
        l = list(lows[start:])
        c0 = float(closes[start]) or 1.0
        w0 = (max(h) - min(l)) / c0
        if not (W0_MIN <= w0 <= W0_MAX):
            return {"status": "rejected_width", "w0": w0}
        u0, su = _linreg(h)
        l0, sl = _linreg(l)
        uE = u0 + su * (lb - 1)
        lE = l0 + sl * (lb - 1)
        width0 = u0 - l0
        falling = su < 0 and sl < 0 and su < sl and width0 > 0 and (uE - lE) < 0.95 * width0
        rising = su > 0 and sl > 0 and su > sl and width0 > 0 and (uE - lE) < 0.95 * width0
        if not (falling or rising):
            return {"status": "no_wedge", "w0": w0}
        return {"status": "rejected_touches", "w0": w0}
    br = scan_breakout_retest(
        highs, lows, opens, closes, form,
        breakout_max=BREAKOUT_MAX_BARS, retest_max=RETEST_MAX_BARS,
    )
    if br is None:
        return {"status": "no_breakout", "form": form}
    out: dict[str, Any] = {
        "status": br.get("status"),
        "form": form,
        "w0": form.w0,
        "wE": form.wE,
        "touches_up": form.touches_up,
        "touches_dn": form.touches_dn,
        "kind": form.kind,
        "lb": lb,
    }
    out.update({k: v for k, v in br.items() if k != "status"})
    if br.get("status") == "signal":
        side = br["side"]
        div = _rsi_divergence(
            highs[form.form_start : form.form_end],
            lows[form.form_start : form.form_end],
            closes[form.form_start : form.form_end],
            side,
        )
        out["div"] = div
        out["side"] = side
    return out
