"""PR-B: live exit engine — IMPORT paper pure rules (not copy).

ENV ZEUS_LIVE_EXIT_ENGINE=shadow|on|off (default shadow).
BE is owned by PR-A (price_breakeven) — this module never emits BE moves.
Hierarchy: liquidation > hard stop > plan. Stop only tightens via apply_tighter semantics.

Rules transferred (paper SMART + safety):
  D1_STRUCTURAL, TRAILING k=2 ATR14, MAE_CUT 0.9R, REGIME full ADVERSE,
  MAX_HOLD 48h, VOL_EXPANSION 2.0, TAKE clamp [2.0,2.3], qty fractions 30/70.
NOT transferred: TIME_STOP, MOMENTUM_EXIT, BREAKEVEN (A owns it).
"""
from __future__ import annotations

import logging
import os
import time
from decimal import Decimal
from typing import Any

from astra_bot.decision.exit_controller import _atr
from astra_bot.decision.exit_manager import ExitManagerConfig, tr_series
from astra_bot.decision.exit_plan import (
    ADVERSE_REGIMES,
    LEVERAGE_TRIGGER_SCALE,
    PLAN_TP_FRACTIONS,
    clamp_take_rr,
    smart_params,
    structural_tighten,
    trailing_target,
)

logger = logging.getLogger(__name__)
SHADOW_EVENT = "exit_shadow"


def engine_mode() -> str:
    v = (os.environ.get("ZEUS_LIVE_EXIT_ENGINE") or "shadow").strip().lower()
    return v if v in ("shadow", "on", "off") else "shadow"


def qty_leg_fractions() -> tuple[float, float]:
    """Volume split only — same tuple paper uses."""
    return PLAN_TP_FRACTIONS  # (0.3, 0.7)


def take_price_clamped(
    entry: Decimal,
    stop: Decimal,
    direction: str,
    take: Decimal | None = None,
) -> Decimal:
    """Single take PRICE after clamp [2.0, 2.3]R. tp1 price == this level.

    Arena: fractions 30/70 apply to QTY, not to distance. Both volume legs
    reference the same clamped take price.
    """
    risk = abs(entry - stop)
    if risk <= 0:
        return entry
    if take is None or take <= 0:
        take = (
            entry + risk * Decimal("2.0")
            if direction == "long"
            else entry - risk * Decimal("2.0")
        )
    return clamp_take_rr(entry, stop, take, direction, 2.0, 2.3)


def take_levels(
    entry: Decimal,
    stop: Decimal,
    direction: str,
    take: Decimal | None = None,
) -> tuple[Decimal, Decimal]:
    """Return (tp1_price, tp_full_price). Both equal clamped take (volume split elsewhere)."""
    px = take_price_clamped(entry, stop, direction, take)
    return px, px


def qty_split(total: Decimal) -> tuple[Decimal, Decimal]:
    f1, f2 = qty_leg_fractions()
    q1 = total * Decimal(str(f1))
    q2 = total - q1
    return q1, q2


def would_regime_exit(direction: str, pos_regime: str | None, current: str) -> bool:
    adverse = ADVERSE_REGIMES.get(direction.lower(), set())
    return current in adverse and current != (pos_regime or "")


def would_mae_cut(
    direction: str,
    entry: float,
    stop: float,
    close: float,
    leverage: float = 1.0,
) -> bool:
    p = smart_params()
    scale = float(LEVERAGE_TRIGGER_SCALE) if float(leverage) > 1 else 1.0
    risk = abs(entry - stop)
    if risk <= 0:
        return False
    r_now = (close - entry) / risk if direction == "long" else (entry - close) / risk
    return r_now <= -float(p.mae_cut_r) * scale


def trail_stop_candidate(
    direction: str,
    current_stop: Decimal,
    highest_price: Decimal | None,
    lowest_price: Decimal | None,
    bars: list[Any],
    k: float | None = None,
) -> Decimal | None:
    """Exact paper trailing_target + _atr(bars, len-2). Guard len>=15 for period=14."""
    if len(bars) < 15:
        return None
    p = smart_params()
    kk = float(k if k is not None else p.trailing_k)
    atr = _atr(list(bars), len(bars) - 2)
    return trailing_target(
        direction,
        Decimal(str(current_stop)),
        highest_price,
        lowest_price,
        atr,
        kk,
    )


def structural_stop_candidate(
    direction: str,
    entry: Decimal,
    current_stop: Decimal,
    closed_bars: list[Any],
) -> Decimal | None:
    """D1 via paper structural_tighten (buffer_pct=0.003 default)."""
    p = smart_params()
    return structural_tighten(
        direction,
        entry,
        current_stop,
        closed_bars,
        lookback=p.structural_lookback,
        buffer_atr=p.structural_buffer_atr,
        buffer_pct=p.structural_buffer_pct,
    )


def max_hold_hit(opened_at_ms: int | None, now_ms: int | None = None) -> bool:
    cfg = ExitManagerConfig()
    if not opened_at_ms:
        return False
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    return (now - int(opened_at_ms)) >= int(cfg.max_hold_seconds) * 1000


def would_vol_expansion(bars: list[Any]) -> bool:
    """VOL_EXPANSION ratio 2.0 via paper tr_series + ExitManagerConfig."""
    cfg = ExitManagerConfig()
    if not bars or len(bars) < max(cfg.vol_lookback, 5):
        return False
    series = tr_series(bars, cfg.vol_window)
    if len(series) < 5:
        return False
    import statistics
    med = statistics.median(series[:-1])
    if med <= 0:
        return False
    return series[-1] >= cfg.vol_expansion_ratio * med


def shadow_row(symbol: str, action: str, **kwargs: Any) -> dict[str, Any]:
    row = {"event": SHADOW_EVENT, "symbol": symbol, "action": action, "ts": time.time()}
    row.update(kwargs)
    return row


def evaluate_plan_actions(
    *,
    symbol: str,
    direction: str,
    entry: Decimal,
    stop: Decimal,
    mark: Decimal,
    highest: Decimal | None,
    lowest: Decimal | None,
    closed_bars: list[Any],
    opened_at_ms: int | None,
    regime: str,
    pos_regime: str | None,
    leverage: float = 1.0,
) -> list[dict[str, Any]]:
    """Pure evaluation: list of shadow/actions. Never BE (PR-A owns BE)."""
    actions: list[dict[str, Any]] = []
    side = direction.lower()
    # forced closes
    if would_mae_cut(side, float(entry), float(stop), float(mark), leverage):
        actions.append(shadow_row(symbol, "mae_cut", mark=str(mark), stop=str(stop)))
    if would_regime_exit(side, pos_regime, regime):
        actions.append(shadow_row(symbol, "regime_exit", regime=regime))
    if max_hold_hit(opened_at_ms):
        actions.append(shadow_row(symbol, "max_hold"))
    if would_vol_expansion(closed_bars):
        actions.append(shadow_row(symbol, "vol_expansion"))
    # stop tightens only
    st = structural_stop_candidate(side, entry, stop, closed_bars)
    if st is not None:
        actions.append(shadow_row(symbol, "d1_structural", stop=str(st), stop_before=str(stop)))
    tr = trail_stop_candidate(side, stop, highest, lowest, closed_bars)
    if tr is not None:
        actions.append(shadow_row(symbol, "trailing", stop=str(tr), stop_before=str(stop)))
    return actions

# --- bar cache: symbol -> (bar_open_ts_ms, bars) ; refresh only on new 4h close ---
_BAR_CACHE: dict[str, tuple[int, list]] = {}


async def _closed_bars_for_plan(client, symbol: str, timeframe: str = "4h") -> list:
    """TF of position with 1h fallback; pull only when a new bar closed."""
    tf = timeframe or "4h"
    try:
        bars = await client.get_candles(symbol, interval=tf, limit=50)
    except Exception:
        bars = []
    if not bars and tf != "1h":
        try:
            bars = await client.get_candles(symbol, interval="1h", limit=50)
        except Exception:
            bars = []
    if not bars:
        return list(_BAR_CACHE.get(symbol, (0, []))[1])
    closed = list(bars[:-1]) if len(bars) > 1 else list(bars)
    if not closed:
        return []
    last = closed[-1]
    ts = int(getattr(last, "open_time", None) or getattr(last, "ts", 0) or 0)
    prev_ts, prev_bars = _BAR_CACHE.get(symbol, (0, []))
    if ts and ts == prev_ts:
        return list(prev_bars)
    _BAR_CACHE[symbol] = (ts, closed)
    return closed


def _update_peak_from_bars(plan: dict, closed_bars: list) -> None:
    """Peak high/low since entry from closed TF bars (trail anchor, not tick mark)."""
    if not closed_bars:
        return
    opened_ms = plan.get("opened_at_ms") or plan.get("ts")
    try:
        opened_ms = int(float(opened_ms) * 1000) if opened_ms and float(opened_ms) < 1e12 else int(opened_ms or 0)
    except Exception:
        opened_ms = 0
    highs, lows = [], []
    for b in closed_bars:
        bt = int(getattr(b, "open_time", None) or getattr(b, "ts", 0) or 0)
        if opened_ms and bt and bt < opened_ms:
            continue
        h = getattr(b, "high", None)
        l = getattr(b, "low", None)
        if h is not None:
            highs.append(float(h))
        if l is not None:
            lows.append(float(l))
    if highs:
        cur = plan.get("peak_high")
        m = max(highs)
        plan["peak_high"] = str(max(float(cur), m) if cur is not None else m)
    if lows:
        cur = plan.get("peak_low")
        m = min(lows)
        plan["peak_low"] = str(min(float(cur), m) if cur is not None else m)


def migrate_open_stops(plans: dict) -> list[dict]:
    """Guarded one-shot: ETH stop 2420.26848 -> 2415.67848 (risk 1.23%->~1%)."""
    out = []
    for k, p in list(plans.items()):
        if p.get("migrate_stop_v1"):
            continue
        if str(p.get("symbol")) != "ETH-USDT":
            continue
        old = str(p.get("stop") or "")
        if not old.startswith("2420.26848"):
            p["migrate_stop_v1"] = True
            continue
        p["stop"] = "2415.67848"
        p["migrate_stop_v1"] = True
        out.append({
            "symbol": "ETH-USDT",
            "side": p.get("side"),
            "stop_before": old,
            "stop": "2415.67848",
        })
    return out


async def run_exit_pass(broker, *, send_tg=None, positions_path=None) -> dict:
    """Tick: migrate first, then paper rules. BE owned by PR-A."""
    from astra_bot.decision.live_arm_wiring import append_journal
    from astra_bot.decision.live_positions import load_positions, upsert_plan
    from astra_bot.decision.live_stop_telemetry import record_stop_change

    mode = engine_mode()
    if mode == "off":
        return {"mode": mode, "actions": 0}
    path = positions_path
    plans = load_positions(path)
    if not plans:
        return {"mode": mode, "actions": 0}

    migrated = migrate_open_stops(plans)
    for m in migrated:
        try:
            sym = m["symbol"]
            side = str(m.get("side") or "short").lower()
            plan = next((p for p in plans.values() if str(p.get("symbol")) == sym), None)
            if not plan:
                continue
            qty = Decimal(str(plan.get("qty") or 0))
            new_stop = Decimal(str(m["stop"]))
            if mode == "on" and qty > 0:
                await broker.move_stop(sym, side, qty, new_stop)
            try:
                record_stop_change(
                    symbol=sym, stop_price=str(new_stop),
                    source="migrate_v1", reason="ETH 1.23pct->1pct", side=side,
                )
            except Exception:
                pass
            try:
                append_journal({"event": "migrate_open_stops", **m, "mode": mode, "ts": time.time()})
            except Exception:
                pass
            upsert_plan(
                symbol=sym, side=side,
                entry=Decimal(str(plan.get("entry") or 0)),
                stop=new_stop, qty=qty, path=path,
                tp=plan.get("tp"), tp1=plan.get("tp1"),
                be_moved=plan.get("be_moved"), signal_id=plan.get("signal_id"),
            )
            plan["stop"] = str(new_stop)
            plan["migrate_stop_v1"] = True
        except Exception as mex:
            logger.warning("migrate_open_stops: %s", type(mex).__name__)

    n = 0
    try:
        positions = await broker.client.get_positions()
    except Exception as exc:
        logger.warning("run_exit_pass positions: %s", type(exc).__name__)
        return {"mode": mode, "error": type(exc).__name__, "migrated": len(migrated)}
    by_sym = {getattr(p, "symbol", "") or "": p for p in positions if getattr(p, "symbol", None)}

    for _key, plan in list(plans.items()):
        sym = str(plan.get("symbol") or _key.split("|")[0] or "")
        if not sym or sym not in by_sym:
            continue
        pos = by_sym[sym]
        try:
            entry = Decimal(str(plan.get("entry") or 0))
            stop = Decimal(str(plan.get("stop") or 0))
            side = str(plan.get("side") or "long").lower()
            mark = Decimal(str(
                getattr(pos, "mark_price", None)
                or getattr(pos, "markPrice", None)
                or getattr(pos, "price", None)
                or entry
            ))
            tf = str(plan.get("timeframe") or plan.get("tf") or "4h")
            closed_bars = await _closed_bars_for_plan(broker.client, sym, tf)
            _update_peak_from_bars(plan, closed_bars)
            peak_h = Decimal(str(plan["peak_high"])) if plan.get("peak_high") else None
            peak_l = Decimal(str(plan["peak_low"])) if plan.get("peak_low") else None
            opened_at_ms = plan.get("opened_at_ms")
            if opened_at_ms is None and plan.get("ts") is not None:
                try:
                    ts = float(plan["ts"])
                    opened_at_ms = int(ts * 1000) if ts < 1e12 else int(ts)
                except Exception:
                    opened_at_ms = None
            actions = evaluate_plan_actions(
                symbol=sym, direction=side, entry=entry, stop=stop, mark=mark,
                highest=peak_h, lowest=peak_l, closed_bars=closed_bars,
                opened_at_ms=opened_at_ms,
                regime=str(plan.get("regime") or ""),
                pos_regime=str(plan.get("regime") or "") or None,
                leverage=float(plan.get("leverage") or 1),
            )
            for act in actions:
                n += 1
                try:
                    append_journal(act)
                except Exception:
                    pass
                if mode != "on":
                    continue
                if act.get("action") in ("d1_structural", "trailing") and act.get("stop"):
                    new_stop = Decimal(str(act["stop"]))
                    qty = Decimal(str(getattr(pos, "quantity", 0) or plan.get("qty") or 0))
                    if qty <= 0:
                        continue
                    await broker.move_stop(sym, side, qty, new_stop)
                    try:
                        record_stop_change(
                            symbol=sym, stop_price=str(new_stop),
                            source=act.get("action"), reason="exit_engine", side=side,
                        )
                    except Exception:
                        pass
                elif act.get("action") in ("mae_cut", "regime_exit", "max_hold", "vol_expansion"):
                    try:
                        await broker.reduce_position_qty(
                            sym, side, Decimal(str(getattr(pos, "quantity", 0) or 0)),
                        )
                    except Exception as cexc:
                        logger.warning("exit_engine close %s: %s", sym, type(cexc).__name__)
        except Exception as exc:
            logger.warning("run_exit_pass %s: %s", sym, type(exc).__name__)
    return {"mode": mode, "actions": n, "migrated": len(migrated)}
