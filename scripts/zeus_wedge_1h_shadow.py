"""Shadow logger for zeus_wedge_retest_1h — append-only jsonl, never trades.

Primary geometry: gold (brief). Optional: narrow. Field geom tags stream.
result_r: 50% at tp1 + remainder at be_stop/tp2/stop; costs 0.0015/side.
Loss streak cumulative (wins/zeros do NOT reset); pause per strategy+symbol.

Production signal path uses lag-scan (formation ends k bars ago) so breakout
bars exist after form_end. Open positions keyed by entry_ts (ms), not bar index.
Dedup/cooldown key = symbol|geom|side|lb (no breakout_ts) with 48h TTL.
Dual lookback streams (lb=48 and lb=72) run in parallel and never suppress each other.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from astra_bot.zeus_wedge_1h_core import (
    BREAKOUT_MAX_BARS,
    COST_PER_SIDE,
    RETEST_MAX_BARS,
    evaluate_bars_with_lag,
    wilder_adx,
)

logger = logging.getLogger(__name__)
SHADOW_PATH = Path("models/zeus_wedge_1h_signals.jsonl")
STATE_PATH = Path("models/zeus_wedge_1h_shadow_state.json")
GEOMS = ("gold", "narrow")
LOOKBACKS: tuple[int, ...] = (48, 72)
DONE_TTL_MS = 48 * 3600 * 1000


def _append(row: dict[str, Any]) -> None:
    SHADOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SHADOW_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _empty_section() -> dict[str, Any]:
    return {"open": {}, "loss_streak": {}, "pause_until": {}, "done": {}}


def _load_state() -> dict[str, Any]:
    """State has independent sections per lb under by_lb."""
    if not STATE_PATH.exists():
        return {"by_lb": {}}
    try:
        st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(st, dict):
            return {"by_lb": {}}
        # Migrate legacy flat open/done into lb=48 section once.
        if "by_lb" not in st and ("open" in st or "done" in st):
            st = {
                "by_lb": {
                    "48": {
                        "open": st.get("open") or {},
                        "done": st.get("done") or {},
                        "loss_streak": st.get("loss_streak") or {},
                        "pause_until": st.get("pause_until") or {},
                    }
                }
            }
        st.setdefault("by_lb", {})
        return st
    except Exception:
        return {"by_lb": {}}


def _save_state(st: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def _lb_section(st: dict[str, Any], lb: int) -> dict[str, Any]:
    by = st.setdefault("by_lb", {})
    key = str(int(lb))
    sec = by.setdefault(key, _empty_section())
    for k in ("open", "done", "loss_streak", "pause_until"):
        sec.setdefault(k, {})
    return sec


def _prune_done(done: dict[str, float], now_ms: int) -> None:
    dead = [k for k, t in done.items() if now_ms - float(t) > DONE_TTL_MS]
    for k in dead:
        del done[k]


def _ohlc(bars: Sequence[Any]):
    o, h, l, c, v, ts = [], [], [], [], [], []
    for b in bars:
        if hasattr(b, "open"):
            o.append(float(b.open))
            h.append(float(b.high))
            l.append(float(b.low))
            c.append(float(b.close))
            v.append(float(getattr(b, "volume", 0) or 0))
            ot = getattr(b, "open_time", None) or getattr(b, "timestamp", None) or 0
            ts.append(int(ot))
        else:
            o.append(float(b["open"]))
            h.append(float(b["high"]))
            l.append(float(b["low"]))
            c.append(float(b["close"]))
            v.append(float(b.get("volume", 0) or 0))
            ot = b.get("open_time") or b.get("timestamp") or 0
            ts.append(int(ot))
    out_ts = [t * 1000 if t and t < 10_000_000_000 else t for t in ts]
    return o, h, l, c, v, out_ts


def _btc_ctx(btc_4h, btc_1h):
    out = {
        "btc_ema50_4h_dist": None,
        "btc_ret10_4h": None,
        "btc_ret24h": None,
        "btc_ret72h": None,
        "adx4h": None,
    }
    if not btc_4h or len(btc_4h) < 55:
        return out

    def _f(b, k):
        return float(getattr(b, k, b.get(k) if isinstance(b, dict) else 0) or 0)

    closes = [_f(b, "close") for b in btc_4h]
    highs = [_f(b, "high") for b in btc_4h]
    lows = [_f(b, "low") for b in btc_4h]
    k = 2.0 / 51
    ema = closes[0]
    for x in closes[1:]:
        ema = x * k + ema * (1 - k)
    out["btc_ema50_4h_dist"] = (closes[-1] - ema) / ema if ema else None
    if len(closes) >= 10 and closes[-10]:
        out["btc_ret10_4h"] = (closes[-1] - closes[-10]) / closes[-10]
    out["adx4h"] = wilder_adx(highs, lows, closes, 14)
    if btc_1h and len(btc_1h) >= 73:
        c1 = [_f(b, "close") for b in btc_1h]
        if c1[-25]:
            out["btc_ret24h"] = (c1[-1] - c1[-25]) / c1[-25]
        if c1[-73]:
            out["btc_ret72h"] = (c1[-1] - c1[-73]) / c1[-73]
    return out


def _dedup_key(symbol: str, geom: str, side: str, lb: int) -> str:
    """Episode key: one open/cooldown per symbol|geom|side|lb (no breakout_ts)."""
    return f"{symbol}|{geom}|{side}|{int(lb)}"


def _log_one(*, symbol, o, h, l, c, v, ts, btc, lb, regime, geom, st):
    result = evaluate_bars_with_lag(
        o, h, l, c, lb=lb, geom=geom,
        max_lag=BREAKOUT_MAX_BARS + RETEST_MAX_BARS + 1,
    )
    status = result.get("status") or "unknown"
    form = result.get("form")
    now_ms = int(time.time() * 1000)
    sid = str(uuid.uuid4())
    sec = _lb_section(st, lb)
    open_map = sec["open"]
    pause_until = sec["pause_until"]
    done = sec["done"]
    _prune_done(done, now_ms)
    side = result.get("side")
    key_sym = f"zeus_wedge_retest_1h|{symbol}"
    breakout_i = result.get("breakout_i")
    retest_i = result.get("retest_i")
    entry_i = result.get("entry_i")
    breakout_ts = ts[breakout_i] if breakout_i is not None and breakout_i < len(ts) else None
    retest_ts = ts[retest_i] if retest_i is not None and retest_i < len(ts) else None
    entry_ts = ts[entry_i] if entry_i is not None and entry_i < len(ts) else None

    # Dedup BEFORE blocked_pause rewrite — suppress episode noise while paused
    if status in ("signal", "signal_pending_entry") and side:
        dkey = _dedup_key(symbol, geom, side, lb)
        if dkey in done:
            return None
        if dkey in open_map:
            return None

    counterfactual_pause = False
    if pause_until.get(key_sym, 0) > time.time():
        counterfactual_pause = True
        if status == "signal":
            status = "blocked_pause"

    atr_pct = None
    if len(h) >= 15:
        trs = [max(h[-i] - l[-i], abs(h[-i] - c[-i - 1]), abs(l[-i] - c[-i - 1])) for i in range(1, 15)]
        atr = sum(trs) / len(trs)
        atr_pct = atr / c[-1] if c[-1] else None
    vol_ratio = None
    if breakout_i is not None and breakout_i >= 20:
        med = sorted(v[breakout_i - 20:breakout_i])[10]
        if med > 0:
            vol_ratio = v[breakout_i] / med
    trend120 = (c[-1] - c[-120]) / c[-120] if len(c) >= 120 and c[-120] else None
    trend_aligned = None
    if side and trend120 is not None:
        trend_aligned = (side == "long" and trend120 > 0) or (side == "short" and trend120 < 0)
    breakout_pos = None
    if form is not None and breakout_i is not None:
        span = form.form_end - form.form_start
        breakout_pos = (breakout_i - form.form_start) / span if span else None

    row = {
        "signal_id": sid, "ts": now_ms, "symbol": symbol, "side": side, "lb": lb, "geom": geom,
        "w0": result.get("w0"), "wE": result.get("wE"),
        "touches_up": result.get("touches_up"), "touches_dn": result.get("touches_dn"),
        "status": status, "breakout_ts": breakout_ts, "retest_ts": retest_ts,
        "entry": result.get("entry"), "stop": result.get("stop"),
        "tp1": result.get("tp1"), "tp2": result.get("tp2"), "rr_to_tp2": result.get("rr_to_tp2"),
        "div": result.get("div"), "hour_utc": time.gmtime().tm_hour,
        "btc_ema50_4h_dist": btc.get("btc_ema50_4h_dist"),
        "btc_ret10_4h": btc.get("btc_ret10_4h"),
        "btc_ret24h": btc.get("btc_ret24h"), "btc_ret72h": btc.get("btc_ret72h"),
        "adx4h": btc.get("adx4h"), "atr_pct_1h": atr_pct,
        "vol_breakout_ratio": vol_ratio, "breakout_pos": breakout_pos,
        "trend120_aligned": trend_aligned, "regime": regime,
        "counterfactual_pause": counterfactual_pause,
        "lag": result.get("lag"),
    }
    _append(row)
    if status == "signal" and side and result.get("entry") is not None and entry_ts is not None:
        key_open = _dedup_key(symbol, geom, side, lb)
        open_map[key_open] = {
            "signal_id": sid, "side": side, "geom": geom, "lb": int(lb),
            "entry": result["entry"], "stop": result["stop"],
            "tp1": result["tp1"], "tp2": result["tp2"],
            "entry_ts": entry_ts, "breakout_ts": breakout_ts,
            "ts": now_ms, "symbol": symbol,
        }
        done[key_open] = now_ms  # 48h cooldown after open; pruned by _prune_done
        _save_state(st)
    return row


def log_wedge_1h_shadow(
    *,
    symbol,
    bars_1h,
    btc_4h=None,
    btc_1h=None,
    lb: int | None = None,
    lbs: Sequence[int] | None = None,
    regime=None,
):
    """Scan each lookback independently on the same bar stream.

    Default windows: LOOKBACKS (48, 72). Pass ``lb=`` for a single window
    (tests / back-compat) or ``lbs=`` for an explicit list.
    """
    windows: list[int]
    if lbs is not None:
        windows = [int(x) for x in lbs]
    elif lb is not None:
        windows = [int(lb)]
    else:
        windows = list(LOOKBACKS)

    bars = list(bars_1h)
    if len(bars) >= 2:
        bars = bars[:-1]
    o, h, l, c, v, ts = _ohlc(bars)
    btc = _btc_ctx(btc_4h, btc_1h)
    st = _load_state()
    last = None
    for win in windows:
        if len(bars) < win + 30:
            # One lb short of window must not block the other stream.
            continue
        for geom in GEOMS:
            last = _log_one(
                symbol=symbol, o=o, h=h, l=l, c=c, v=v, ts=ts,
                btc=btc, lb=win, regime=regime, geom=geom, st=st,
            )
    return last


def update_open_shadows(*, symbol: str, bars_1h: Sequence[Any]) -> None:
    """Close open shadows using entry_ts (stable across sliding windows)."""
    st = _load_state()
    by_lb = st.get("by_lb") or {}
    if not by_lb:
        return
    bars = list(bars_1h)
    if len(bars) >= 2:
        bars = bars[:-1]
    _, h, l, c, _, ts = _ohlc(bars)
    if not c or not ts:
        return
    changed = False
    for _lb_key, sec in by_lb.items():
        open_map = sec.setdefault("open", {})
        loss_streak = sec.setdefault("loss_streak", {})
        pause_until = sec.setdefault("pause_until", {})
        if not open_map:
            continue
        for key in list(open_map.keys()):
            if not key.startswith(symbol + "|"):
                continue
            pos = open_map[key]
            side = pos["side"]
            entry, stop = float(pos["entry"]), float(pos["stop"])
            tp1, tp2 = float(pos["tp1"]), float(pos["tp2"])
            risk = abs(entry - stop) or 1e-12
            entry_ts = pos.get("entry_ts")
            if entry_ts is not None:
                entry_i = None
                for i, t in enumerate(ts):
                    if t <= entry_ts:
                        entry_i = i
                    else:
                        break
                if entry_i is None:
                    exit_px = float(c[-1])
                    if side == "long":
                        result_r = (exit_px - entry) / risk - 2 * COST_PER_SIDE * entry / risk
                    else:
                        result_r = (entry - exit_px) / risk - 2 * COST_PER_SIDE * entry / risk
                    _append({
                        "signal_id": pos["signal_id"], "ts": int(time.time() * 1000),
                        "symbol": symbol, "side": side, "geom": pos.get("geom"),
                        "lb": pos.get("lb"),
                        "status": "closed", "result_r": result_r, "mfe_r": 0.0, "mae_r": 0.0,
                        "hold_hours": None, "exit_reason": "eod",
                    })
                    del open_map[key]
                    changed = True
                    continue
            else:
                entry_i = 0
            exit_reason = None
            exit_px = None
            tp1_hit = False
            be = False
            mfe = mae = 0.0
            for i in range(entry_i + 1, len(c)):
                hi, lo = h[i], l[i]
                if side == "long":
                    mfe = max(mfe, (hi - entry) / risk)
                    mae = min(mae, (lo - entry) / risk)
                    if not tp1_hit and hi >= tp1:
                        tp1_hit = be = True
                        stop = entry
                    if lo <= stop:
                        exit_reason = "be_stop" if be and stop == entry else "stop"
                        exit_px = stop
                        break
                    if hi >= tp2:
                        exit_reason, exit_px = "tp2", tp2
                        break
                else:
                    mfe = max(mfe, (entry - lo) / risk)
                    mae = min(mae, (entry - hi) / risk)
                    if not tp1_hit and lo <= tp1:
                        tp1_hit = be = True
                        stop = entry
                    if hi >= stop:
                        exit_reason = "be_stop" if be and stop == entry else "stop"
                        exit_px = stop
                        break
                    if lo <= tp2:
                        exit_reason, exit_px = "tp2", tp2
                        break
            if exit_reason is None:
                continue
            cost_r = 2 * COST_PER_SIDE * entry / risk
            if side == "long":
                r_tp1 = (tp1 - entry) / risk
                r_final = (float(exit_px) - entry) / risk
            else:
                r_tp1 = (entry - tp1) / risk
                r_final = (entry - float(exit_px)) / risk
            if tp1_hit:
                if exit_reason == "be_stop":
                    result_r = 0.5 * r_tp1 + 0.5 * 0.0 - cost_r
                else:
                    result_r = 0.5 * r_tp1 + 0.5 * r_final - cost_r
            else:
                result_r = r_final - cost_r
            _append({
                "signal_id": pos["signal_id"], "ts": int(time.time() * 1000),
                "symbol": symbol, "side": side, "geom": pos.get("geom"),
                "lb": pos.get("lb"),
                "status": "closed", "result_r": result_r, "mfe_r": mfe, "mae_r": mae,
                "hold_hours": max(0, len(c) - entry_i - 1), "exit_reason": exit_reason,
            })
            key_sym = f"zeus_wedge_retest_1h|{symbol}"
            if result_r < 0:
                loss_streak[key_sym] = int(loss_streak.get(key_sym, 0)) + 1
                if loss_streak[key_sym] >= 3:
                    pause_until[key_sym] = time.time() + 36 * 3600
                    loss_streak[key_sym] = 0
            del open_map[key]
            changed = True
    if changed:
        _save_state(st)


async def observe_wedge_1h_shadow(
    *,
    bingx: Any,
    symbol: str,
    regime: str | None = None,
    lbs: Sequence[int] | None = None,
    lb: int | None = None,
) -> None:
    """Fetch 1h bars once; scan each lookback window independently."""
    windows = list(lbs) if lbs is not None else ([int(lb)] if lb is not None else list(LOOKBACKS))
    try:
        need = max(max(windows) + 80, 200)
        klines = await bingx.get_candles(symbol, "1h", limit=need)
        bars = list(klines) if klines else []
        btc_1h = btc_4h = None
        try:
            btc_1h = list(await bingx.get_candles("BTC-USDT", "1h", limit=100) or [])
            btc_4h = list(await bingx.get_candles("BTC-USDT", "4h", limit=80) or [])
        except Exception:
            pass
        log_wedge_1h_shadow(
            symbol=symbol, bars_1h=bars, btc_4h=btc_4h, btc_1h=btc_1h, lbs=windows, regime=regime,
        )
        update_open_shadows(symbol=symbol, bars_1h=bars)
    except Exception as exc:
        logger.debug("wedge_1h_shadow: %s", exc)
