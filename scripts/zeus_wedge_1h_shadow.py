"""Shadow logger for zeus_wedge_retest_1h — append-only jsonl, never trades."""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

from astra_bot.zeus_wedge_1h_core import COST_PER_SIDE, evaluate_bars

logger = logging.getLogger(__name__)

SHADOW_PATH = Path("models/zeus_wedge_1h_signals.jsonl")
STATE_PATH = Path("models/zeus_wedge_1h_shadow_state.json")


def _append(row: dict[str, Any]) -> None:
    SHADOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SHADOW_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"open": {}, "loss_streak": {}, "pause_until": {}}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"open": {}, "loss_streak": {}, "pause_until": {}}


def _save_state(st: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def _ohlc(bars: Sequence[Any]):
    o, h, l, c, v = [], [], [], [], []
    for b in bars:
        if hasattr(b, "open"):
            o.append(float(b.open)); h.append(float(b.high))
            l.append(float(b.low)); c.append(float(b.close))
            v.append(float(getattr(b, "volume", 0) or 0))
        else:
            o.append(float(b["open"])); h.append(float(b["high"]))
            l.append(float(b["low"])); c.append(float(b["close"]))
            v.append(float(b.get("volume", 0) or 0))
    return o, h, l, c, v


def _btc_ctx(btc_4h, btc_1h):
    out = {
        "btc_ema50_4h_dist": None,
        "btc_ema50_4h_slope": None,
        "btc_ret24h": None,
        "btc_ret72h": None,
        "adx4h": None,
    }
    if not btc_4h or len(btc_4h) < 55:
        return out
    closes = [float(getattr(b, "close", b.get("close") if isinstance(b, dict) else 0)) for b in btc_4h]
    k = 2.0 / 51
    ema = closes[0]
    for x in closes[1:]:
        ema = x * k + ema * (1 - k)
    out["btc_ema50_4h_dist"] = (closes[-1] - ema) / ema if ema else None
    if len(closes) >= 10 and closes[-10]:
        out["btc_ema50_4h_slope"] = (closes[-1] - closes[-10]) / closes[-10]
    if btc_1h and len(btc_1h) >= 73:
        c1 = [float(getattr(b, "close", b.get("close") if isinstance(b, dict) else 0)) for b in btc_1h]
        if c1[-25]:
            out["btc_ret24h"] = (c1[-1] - c1[-25]) / c1[-25]
        if c1[-73]:
            out["btc_ret72h"] = (c1[-1] - c1[-73]) / c1[-73]
    return out


def log_wedge_1h_shadow(
    *,
    symbol: str,
    bars_1h: Sequence[Any],
    btc_4h=None,
    btc_1h=None,
    lb: int = 48,
    regime: str | None = None,
):
    if len(bars_1h) < lb + 30:
        return None
    bars = list(bars_1h)
    if len(bars) >= 2:
        bars = bars[:-1]
    o, h, l, c, v = _ohlc(bars)
    result = evaluate_bars(o, h, l, c, lb=lb)
    status = result.get("status") or "unknown"
    form = result.get("form")
    now_ms = int(time.time() * 1000)
    sid = str(uuid.uuid4())
    st = _load_state()
    open_map = st.setdefault("open", {})
    loss_streak = st.setdefault("loss_streak", {})
    pause_until = st.setdefault("pause_until", {})
    side = result.get("side")
    key_side = f"{symbol}|{side}" if side else None
    counterfactual_pause = False
    if key_side and pause_until.get(key_side, 0) > time.time():
        counterfactual_pause = True
        if status == "signal":
            status = "blocked_pause"
    if status == "signal" and key_side and key_side in open_map:
        return None
    btc = _btc_ctx(btc_4h, btc_1h)
    hour_utc = time.gmtime().tm_hour
    atr_pct = None
    if len(h) >= 15:
        trs = [max(h[-i] - l[-i], abs(h[-i] - c[-i - 1]), abs(l[-i] - c[-i - 1])) for i in range(1, 15)]
        atr = sum(trs) / len(trs)
        atr_pct = atr / c[-1] if c[-1] else None
    vol_ratio = None
    breakout_i = result.get("breakout_i")
    if breakout_i is not None and breakout_i >= 20:
        med = sorted(v[breakout_i - 20 : breakout_i])[10]
        if med > 0:
            vol_ratio = v[breakout_i] / med
    trend120 = None
    if len(c) >= 120 and c[-120]:
        trend120 = (c[-1] - c[-120]) / c[-120]
    trend_aligned = None
    if side and trend120 is not None:
        trend_aligned = (side == "long" and trend120 > 0) or (side == "short" and trend120 < 0)
    breakout_pos = None
    if form is not None and breakout_i is not None:
        span = form.form_end - form.form_start
        breakout_pos = (breakout_i - form.form_start) / span if span else None
    row = {
        "signal_id": sid,
        "ts": now_ms,
        "symbol": symbol,
        "side": side,
        "lb": lb,
        "w0": result.get("w0"),
        "wE": result.get("wE"),
        "touches_up": result.get("touches_up"),
        "touches_dn": result.get("touches_dn"),
        "status": status,
        "breakout_ts": breakout_i,
        "retest_ts": result.get("retest_i"),
        "entry": result.get("entry"),
        "stop": result.get("stop"),
        "tp1": result.get("tp1"),
        "tp2": result.get("tp2"),
        "rr_to_tp2": result.get("rr_to_tp2"),
        "div": result.get("div"),
        "hour_utc": hour_utc,
        "btc_ema50_4h_dist": btc.get("btc_ema50_4h_dist"),
        "btc_ema50_4h_slope": btc.get("btc_ema50_4h_slope"),
        "btc_ret24h": btc.get("btc_ret24h"),
        "btc_ret72h": btc.get("btc_ret72h"),
        "adx4h": btc.get("adx4h"),
        "atr_pct_1h": atr_pct,
        "vol_breakout_ratio": vol_ratio,
        "breakout_pos": breakout_pos,
        "trend120_aligned": trend_aligned,
        "regime": regime,
        "counterfactual_pause": counterfactual_pause,
    }
    _append(row)
    if status == "signal" and key_side and result.get("entry") is not None:
        open_map[key_side] = {
            "signal_id": sid,
            "side": side,
            "entry": result["entry"],
            "stop": result["stop"],
            "tp1": result["tp1"],
            "tp2": result["tp2"],
            "entry_i": result.get("entry_i"),
            "ts": now_ms,
        }
        _save_state(st)
    return row


def update_open_shadows(*, symbol: str, bars_1h: Sequence[Any]) -> None:
    st = _load_state()
    open_map = st.setdefault("open", {})
    loss_streak = st.setdefault("loss_streak", {})
    pause_until = st.setdefault("pause_until", {})
    if not open_map:
        return
    bars = list(bars_1h)
    if len(bars) >= 2:
        bars = bars[:-1]
    o, h, l, c, v = _ohlc(bars)
    if not c:
        return
    changed = False
    for key in list(open_map.keys()):
        if not key.startswith(symbol + "|"):
            continue
        pos = open_map[key]
        side = pos["side"]
        entry = float(pos["entry"])
        stop = float(pos["stop"])
        tp1 = float(pos["tp1"])
        tp2 = float(pos["tp2"])
        risk = abs(entry - stop) or 1e-12
        entry_i = int(pos.get("entry_i") or 0)
        mfe = 0.0
        mae = 0.0
        exit_reason = None
        exit_px = None
        be = False
        for i in range(max(entry_i + 1, 0), len(c)):
            hi, lo = h[i], l[i]
            if side == "long":
                mfe = max(mfe, (hi - entry) / risk)
                mae = min(mae, (lo - entry) / risk)
                if not be and hi >= tp1:
                    be = True
                    stop = entry
                if lo <= stop:
                    exit_reason = "be_stop" if be and stop == entry else "stop"
                    exit_px = stop
                    break
                if hi >= tp2:
                    exit_reason = "tp2"
                    exit_px = tp2
                    break
            else:
                mfe = max(mfe, (entry - lo) / risk)
                mae = min(mae, (entry - hi) / risk)
                if not be and lo <= tp1:
                    be = True
                    stop = entry
                if hi >= stop:
                    exit_reason = "be_stop" if be and stop == entry else "stop"
                    exit_px = stop
                    break
                if lo <= tp2:
                    exit_reason = "tp2"
                    exit_px = tp2
                    break
        if exit_reason is None:
            continue
        if side == "long":
            raw = (float(exit_px) - entry) / risk
        else:
            raw = (entry - float(exit_px)) / risk
        cost_r = 2 * COST_PER_SIDE * entry / risk
        result_r = raw - cost_r
        hold_hours = max(0, len(c) - entry_i - 1)
        _append({
            "signal_id": pos["signal_id"],
            "ts": int(time.time() * 1000),
            "symbol": symbol,
            "side": side,
            "status": "closed",
            "result_r": result_r,
            "mfe_r": mfe,
            "mae_r": mae,
            "hold_hours": hold_hours,
            "exit_reason": exit_reason,
        })
        if result_r < 0:
            loss_streak[key] = int(loss_streak.get(key, 0)) + 1
            if loss_streak[key] >= 3:
                pause_until[key] = time.time() + 36 * 3600
                loss_streak[key] = 0
        del open_map[key]
        changed = True
    if changed:
        _save_state(st)


async def observe_wedge_1h_shadow(*, bingx: Any, symbol: str, regime: str | None = None, lb: int = 48) -> None:
    try:
        klines = await bingx.get_candles(symbol, "1h", limit=max(lb + 80, 200))
        bars = list(klines) if klines else []
        btc_1h = btc_4h = None
        try:
            btc_1h = list(await bingx.get_candles("BTC-USDT", "1h", limit=100) or [])
            btc_4h = list(await bingx.get_candles("BTC-USDT", "4h", limit=80) or [])
        except Exception:
            pass
        log_wedge_1h_shadow(symbol=symbol, bars_1h=bars, btc_4h=btc_4h, btc_1h=btc_1h, lb=lb, regime=regime)
        update_open_shadows(symbol=symbol, bars_1h=bars)
    except Exception as exc:
        logger.debug("wedge_1h_shadow: %s", exc)
