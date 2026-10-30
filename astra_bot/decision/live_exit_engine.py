"""PR-B: live exit engine — IMPORT paper pure rules (not copy).

ENV ZEUS_LIVE_EXIT_ENGINE=shadow|on|off (default shadow).
ENV ZEUS_LIVE_TRAIL_POLICY=atr|ratchet_e (default atr). F18: ratchet_e shadow parallel.
BE is owned by PR-A (price_breakeven) — this module never emits BE moves.
Hierarchy: liquidation > hard stop > plan. Stop only tightens via apply_tighter semantics.

Rules transferred (paper SMART + safety):
  D1_STRUCTURAL, TRAILING k=2 ATR14, MAE_CUT 0.9R, REGIME full ADVERSE,
  MAX_HOLD 48h, VOL_EXPANSION 2.0, TAKE clamp [2.0,2.3], qty fractions 30/70.
NOT transferred: TIME_STOP, MOMENTUM_EXIT, BREAKEVEN (A owns it).
"""
from __future__ import annotations

import logging
import math
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
    f1, _f2 = qty_leg_fractions()
    q1 = total * Decimal(str(f1))
    q2 = total - q1
    return q1, q2


def would_regime_exit(direction: str, pos_regime: str | None, current: str) -> bool:
    """True when *current* market regime is adverse and differs from entry regime.

    pos_regime = regime **at entry** (plan.entry_regime or plan.regime).
    current = regime **now** (plan.current_regime when set by the tick).
    TODO(arena/#238 R1): wire live RegimeDetectorV2 on closed_bars each tick so
    current is not stuck equal to entry; until then regime_exit only fires when
    the plan carries a distinct current_regime field.
    """
    adverse = ADVERSE_REGIMES.get(direction.lower(), set())
    return bool(current) and current in adverse and current != (pos_regime or "")


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


def trail_policy() -> str:
    """ZEUS_LIVE_TRAIL_POLICY=atr|ratchet_e (default atr)."""
    v = (os.environ.get("ZEUS_LIVE_TRAIL_POLICY") or "atr").strip().lower()
    return v if v in ("atr", "ratchet_e") else "atr"


def stop_pull_pct() -> float:
    """ZEUS_STOP_PULL_PCT — fraction of entry to lock as profit (default 0 = off)."""
    raw = (os.environ.get("ZEUS_STOP_PULL_PCT") or "0").strip()
    try:
        v = float(raw)
        if v <= 0:
            return 0.0
        return v if v < 0.5 else 0.0
    except ValueError:
        return 0.0


def pct_profit_stop_candidate(
    direction: str,
    entry: Decimal,
    current_stop: Decimal,
    mark: Decimal | None,
    *,
    pct: float | None = None,
) -> Decimal | None:
    """Pull protective stop to lock ~pct profit once price moved pct in our favor.

    Owner 29.10 (VST): not a full take-profit — only tighten stop.
    Long: mark >= entry*(1+pct) → stop = entry*(1+pct)
    Short: mark <= entry*(1-pct) → stop = entry*(1-pct)
    (Locks ~pct profit if stop is hit on reverse.) Only-tighten vs current_stop.
    """
    if mark is None:
        return None
    side = direction.lower()
    entry = Decimal(str(entry))
    cur = Decimal(str(current_stop))
    m = Decimal(str(mark))
    p = Decimal(str(pct if pct is not None else stop_pull_pct()))
    if p <= 0:
        return None
    if side == "long":
        trigger = entry * (Decimal("1") + p)
        if m < trigger:
            return None
        cand = trigger
        if cand > cur:  # tighten long stop upward
            return cand
        return None
    if side == "short":
        trigger = entry * (Decimal("1") - p)
        if m > trigger:
            return None
        cand = trigger
        if cand < cur:  # tighten short stop downward
            return cand
        return None
    return None



def stop_exchange_valid(direction: str, cand: Decimal, mark: Decimal | None) -> bool:
    """F19-1: long stop must be below mark; short stop must be above mark."""
    if mark is None:
        return True  # fail-open when mark unknown
    side = direction.lower()
    c = Decimal(str(cand))
    m = Decimal(str(mark))
    if side == "long":
        return c < m
    if side == "short":
        return c > m
    return True


def classify_current_regime(closed_bars: list[Any]) -> tuple[str, str]:
    """F11-2 / F20 / F21: RegimeEngine on closed TF bars → (regime, reason).

    reason: ok | bars_lt_60 | bars_lt_200_ema | engine_error | unknown.
    RegimeEngine needs EMA200 → >=200 closes (paper parity). Fail-open → ("", reason).
    """
    if not closed_bars or len(closed_bars) < 60:
        return "", "bars_lt_60"
    try:
        from types import SimpleNamespace

        from astra_bot.decision.regime_engine import RegimeEngine

        candles: list[Any] = []
        for b in closed_bars:
            try:
                candles.append(
                    SimpleNamespace(
                        open_time=int(
                            getattr(b, "open_time", None) or getattr(b, "ts", 0) or 0
                        ),
                        open=float(b.open),
                        high=float(b.high),
                        low=float(b.low),
                        close=float(b.close),
                        volume=float(getattr(b, "volume", 0) or 0),
                    )
                )
            except Exception:
                candles.append(b)
        if len(candles) < 60:
            return "", "bars_lt_60"
        # EMA200 gate (indicators.ema returns None if len < period)
        if len(candles) < 200:
            return "", "bars_lt_200_ema"
        report = RegimeEngine().classify(candles)
        reg = getattr(report.regime, "value", None) or str(report.regime)
        if not reg or reg == "UNKNOWN":
            detail = ""
            try:
                detail = str((report.details or {}).get("reason") or "")
            except Exception:
                detail = ""
            reason = "not_enough_ema" if "EMA" in detail else (detail or "unknown")
            return "", reason
        return str(reg), "ok"
    except Exception as exc:
        logger.warning("classify_current_regime: %s", type(exc).__name__)
        return "", "engine_error"




def be_gate_mfe_allows(mfe_r: float, *, threshold: float = 0.5, be_moved: bool = False) -> bool:
    """True if ratchet/BE may move stop to entry (anti-scratch gate).

    Owner 28.10: require MFE >= 0.5R before BE. When be_moved already True, gate N/A.
    """
    if be_moved:
        return False  # BE already done — step-lock path only
    try:
        return float(mfe_r) >= float(threshold)
    except (TypeError, ValueError):
        return False

def ratchet_e_stop_candidate(
    direction: str,
    entry: Decimal,
    current_stop: Decimal,
    highest_price: Decimal | None,
    lowest_price: Decimal | None,
    *,
    initial_stop: Decimal | None = None,
    mark: Decimal | None = None,
    be_moved: bool = False,
) -> Decimal | None:
    """Owner ratchet E: BE when MFE>=0.5R (anti-scratch 28.10); MFE steps 0.5R → stop = trigger−0.5R.

    Only tightens (long: stop rises; short: stop falls). No take-profit.
    Examples: MFE 1.0R→stop +0.5R; 1.5R→+1.0R; 2.0R→+1.5R from entry.

    F19-2: when be_moved=True, BE is owned by PR-A — skip BE-only candidate
    (mfe in [0, 1)); still emit step-lock levels (mfe >= 1.0, only-tighten).
    Owner 28.10: BE gate MFE>=0.5R (blocks mfe in [0, 0.5)).
    """
    side = direction.lower()
    entry = Decimal(str(entry))
    stop0 = Decimal(str(initial_stop if initial_stop is not None else current_stop))
    risk = abs(entry - stop0)
    if risk <= 0:
        return None
    hi = highest_price
    lo = lowest_price
    if mark is not None:
        m = Decimal(str(mark))
        if hi is None or m > hi:
            hi = m
        if lo is None or m < lo:
            lo = m
    if side == "long":
        if hi is None:
            return None
        mfe_r = float((Decimal(str(hi)) - entry) / risk)
    else:
        if lo is None:
            return None
        mfe_r = float((entry - Decimal(str(lo))) / risk)
    cand: Decimal | None = None
    # Owner 28.10 anti-scratch: BE only when MFE >= 0.5R (was mfe>=0 → fee scratches).
    # PR-A still owns BE when be_moved; ratchet must not pull stop to entry on noise.
    BE_GATE_MFE_R = 0.5
    if (not be_moved) and mfe_r >= BE_GATE_MFE_R:
        cand = entry
    # Staircase locks (also on be_moved):
    # - classic: MFE>=1.0 → floor(mfe/0.5)*0.5 - 0.5  (1.0R→0.5R, 1.5→1.0, …)
    # - owner 30.10 profit-leak: after BE, first lock at MFE>=0.75 → 0.25R profit
    #   so runners do not sit at entry while price already gave 0.8–0.9R.
    if mfe_r >= 1.0:
        locked = math.floor(mfe_r / 0.5) * 0.5 - 0.5
        if locked > 0:
            delta = risk * Decimal(str(locked))
            cand = entry + delta if side == "long" else entry - delta
    elif be_moved and mfe_r >= 0.75:
        delta = risk * Decimal("0.25")
        cand = entry + delta if side == "long" else entry - delta
    if cand is None:
        return None
    cur = Decimal(str(current_stop))
    if side == "long" and cand > cur:
        return cand
    if side == "short" and cand < cur:
        return cand
    return None



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
    initial_stop: Decimal | None = None,
    be_moved: bool = False,
) -> list[dict[str, Any]]:
    """Pure evaluation: list of shadow/actions. Never BE (PR-A owns BE).

    F18: shadow emits both trailing (ATR) and ratchet_e when each has a
    candidate; live apply uses ZEUS_LIVE_TRAIL_POLICY (default atr).
    """
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
    # F18: ATR trail + ratchet_e in parallel (shadow); policy picks live apply
    tr = trail_stop_candidate(side, stop, highest, lowest, closed_bars)
    if tr is not None:
        actions.append(shadow_row(symbol, "trailing", stop=str(tr), stop_before=str(stop)))
    init = initial_stop if initial_stop is not None else stop
    rat = ratchet_e_stop_candidate(
        side, entry, stop, highest, lowest, initial_stop=init, mark=mark,
        be_moved=bool(be_moved),
    )
    if rat is not None:
        actions.append(
            shadow_row(
                symbol, "ratchet_e", stop=str(rat), stop_before=str(stop),
                trail_policy=trail_policy(),
            )
        )
    # Owner 29.10: lock ~pct profit by pulling protective stop (not full TP)
    pull = pct_profit_stop_candidate(side, entry, stop, mark)
    if pull is not None:
        actions.append(
            shadow_row(
                symbol, "pct_profit_pull", stop=str(pull), stop_before=str(stop),
                pull_pct=stop_pull_pct(), mark=str(mark), entry=str(entry),
            )
        )
    return actions

# --- bar cache: symbol -> (bar_open_ts_ms, bars) ; refresh only on new 4h close ---
_BAR_CACHE: dict[str, tuple[int, list]] = {}


async def _closed_bars_for_plan(client, symbol: str, timeframe: str = "4h") -> list:
    """TF of position with 1h fallback; pull only when a new bar closed.

    F20: BingXClient.get_candles(symbol, timeframe, limit=...) — NOT interval=.
    limit>=250 so RegimeEngine EMA200 + ATR(14) have enough bars (F21).
    """
    tf = timeframe or "4h"
    # F21: RegimeEngine EMA200 needs >=200 closes; keep +buffer for open bar strip
    lim = 250
    bars: list = []
    try:
        # positional timeframe matches BingXClient.get_candles(symbol, timeframe, ...)
        bars = await client.get_candles(symbol, tf, limit=lim)
    except TypeError:
        # alternate signature: timeframe= kw
        try:
            bars = await client.get_candles(symbol, timeframe=tf, limit=lim)
        except Exception as exc:
            logger.warning("_closed_bars_for_plan %s %s: %s", symbol, tf, type(exc).__name__)
            bars = []
    except Exception as exc:
        logger.warning("_closed_bars_for_plan %s %s: %s", symbol, tf, type(exc).__name__)
        bars = []
    if not bars and tf != "1h":
        try:
            bars = await client.get_candles(symbol, "1h", limit=lim)
        except TypeError:
            try:
                bars = await client.get_candles(symbol, timeframe="1h", limit=lim)
            except Exception:
                bars = []
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
    for _k, p in list(plans.items()):
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



async def _exchange_stop_price(broker, symbol: str, side: str) -> Decimal | None:
    """Best-effort current STOP price on exchange for only-tighten gate."""
    try:
        orders = await broker.client.get_open_orders(symbol)
    except Exception as exc:
        logger.warning("_exchange_stop_price %s: %s", symbol, type(exc).__name__)
        return None
    side_l = (side or "").lower()
    best = None
    for o in orders or []:
        typ = str(getattr(o, "type", "") or getattr(o, "order_type", "") or "").upper()
        if "STOP" not in typ or "TAKE_PROFIT" in typ:
            continue
        # position side filter when present
        ps = str(getattr(o, "position_side", "") or getattr(o, "positionSide", "") or "").lower()
        if ps and ps not in ("both", "") and ps != side_l and not (
            (side_l == "long" and ps == "long") or (side_l == "short" and ps == "short")
        ):
            # also accept empty
            if ps in ("long", "short") and ps != side_l:
                continue
        sp = getattr(o, "stop_price", None) or getattr(o, "stopPrice", None) or getattr(o, "trigger_price", None)
        if sp is None:
            continue
        try:
            sp_d = Decimal(str(sp))
        except Exception:
            continue
        if best is None:
            best = sp_d
        else:
            # short: tighter = lower; long: tighter = higher
            if side_l == "short":
                best = min(best, sp_d)
            else:
                best = max(best, sp_d)
    return best


def _is_only_tighten(side: str, new_stop: Decimal, current_stop: Decimal) -> bool:
    """True if new_stop tightens or equals current (never loosens)."""
    if side == "long":
        return new_stop >= current_stop
    return new_stop <= current_stop


async def run_exit_pass(broker, *, send_tg=None, positions_path=None) -> dict:
    """Tick: migrate first, then paper rules. BE owned by PR-A."""
    from astra_bot.decision.live_arm_wiring import append_journal
    from astra_bot.decision.live_positions import load_positions, save_positions, upsert_plan
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
            # F20 / F7.4: LiveBingX Position stores mark in current_price
            # (same source as be_mark_tick / sync_partial_breakeven).
            mark_raw = (
                getattr(pos, "mark_price", None)
                or getattr(pos, "markPrice", None)
                or getattr(pos, "current_price", None)
                or getattr(pos, "price", None)
            )
            mark_fallback = False
            if mark_raw is None:
                mark = entry
                mark_fallback = True
                try:
                    append_journal({
                        "event": "exit_shadow",
                        "action": "mark_fallback",
                        "symbol": sym,
                        "side": side,
                        "mark": str(entry),
                        "reason": "no_mark_on_position",
                    })
                except Exception:
                    pass
            else:
                mark = Decimal(str(mark_raw))
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
            # F20: regime_tick EVERY tick (ok / bars_lt_60 / engine_error / unknown)
            pos_regime = str(plan.get("entry_regime") or plan.get("regime") or "") or None
            regime_now, regime_reason = classify_current_regime(closed_bars)
            if regime_now:
                plan["current_regime"] = regime_now
            try:
                append_journal({
                    "event": "regime_tick",
                    "symbol": sym,
                    "side": side,
                    "regime": regime_now or None,
                    "reason": regime_reason,
                    "entry_regime": pos_regime,
                    "n_bars": len(closed_bars),
                    "mark": str(mark),
                    "mark_fallback": mark_fallback,
                })
            except Exception:
                pass
            if not regime_now:
                regime_now = str(plan.get("current_regime") or "").strip()
            init_stop = plan.get("initial_stop")
            try:
                init_dec = Decimal(str(init_stop)) if init_stop not in (None, "") else stop
            except Exception:
                init_dec = stop
            actions = evaluate_plan_actions(
                symbol=sym, direction=side, entry=entry, stop=stop, mark=mark,
                highest=peak_h, lowest=peak_l, closed_bars=closed_bars,
                opened_at_ms=opened_at_ms,
                regime=regime_now,
                pos_regime=pos_regime,
                leverage=float(plan.get("leverage") or 1),
                initial_stop=init_dec,
                be_moved=bool(plan.get("be_moved")),
            )
            policy = trail_policy()
            for act in actions:
                n += 1
                try:
                    append_journal(act)
                except Exception:
                    pass
                if mode != "on":
                    continue  # shadow: journal only, never place_order/move_stop
                act_name = act.get("action")
                # trail apply: atr→trailing; ratchet_e→ratchet_e;
                # Owner 30.10 hybrid: after be_moved, also apply ratchet step-locks
                # under atr policy so runners do not sit at entry while MFE≥1R.
                be_done = bool(plan.get("be_moved"))
                if (act_name == "d1_structural" and act.get("stop")) or (
                    act_name == "trailing" and policy == "atr" and act.get("stop")
                ) or (
                    act_name == "ratchet_e"
                    and act.get("stop")
                    and (policy == "ratchet_e" or (policy == "atr" and be_done))
                ) or (act_name == "pct_profit_pull" and act.get("stop")):
                    apply_stop = True
                else:
                    apply_stop = False
                if apply_stop:
                    new_stop = Decimal(str(act["stop"]))
                    qty = Decimal(str(getattr(pos, "quantity", 0) or plan.get("qty") or 0))
                    if qty <= 0:
                        continue
                    # F24c: visible marker when protecting under kill-halt
                    if getattr(broker, "_halted", False):
                        try:
                            append_journal({
                                "event": "exit_apply_under_halt",
                                "symbol": sym,
                                "side": side,
                                "action": act_name,
                                "stop": str(new_stop),
                                "halt_reason": getattr(broker, "_halt_reason", None),
                                "ts": time.time(),
                            })
                        except Exception:
                            pass
                    # F19-1: exchange-validity gate (long cand < mark; short cand > mark)
                    if not stop_exchange_valid(side, new_stop, mark):
                        try:
                            append_journal({
                                "event": "exit_shadow",
                                "action": "defer_ratchet",
                                "symbol": sym,
                                "side": side,
                                "stop": str(new_stop),
                                "stop_before": str(stop),
                                "mark": str(mark),
                                "reason": "exchange_invalid",
                            })
                        except Exception:
                            pass
                        continue
                    # Owner 28.10 only-tighten vs EXCHANGE stop (not stale plan stop).
                    exch_stop = await _exchange_stop_price(broker, sym, side)
                    ref_stop = exch_stop if exch_stop is not None else stop
                    if not _is_only_tighten(side, new_stop, ref_stop):
                        try:
                            append_journal({
                                "event": "STOP_UPDATE",
                                "symbol": sym,
                                "side": side,
                                "stop_before": str(ref_stop),
                                "stop_after": str(new_stop),
                                "source": "exchange" if exch_stop is not None else "plan",
                                "ok": False,
                                "reason": "would_loosen",
                                "action": act_name,
                                "ts": time.time(),
                            })
                        except Exception:
                            pass
                        continue
                    await broker.move_stop(sym, side, qty, new_stop)
                    # Owner 28.10: keep plan stop in sync after successful move
                    # so next only-tighten compare is not vs a stale plan level.
                    plan["stop"] = str(new_stop)
                    stop = new_stop
                    if act_name in ("ratchet_e", "trailing", "d1_structural"):
                        try:
                            if abs(new_stop - entry) <= (abs(entry) * Decimal("0.0001") + Decimal("1e-12")):
                                plan["be_moved"] = True
                        except Exception:
                            pass
                    if act_name == "pct_profit_pull":
                        try:
                            from astra_bot.decision.live_arm_wiring import append_journal as _aj
                            _aj({
                                "event": "stop_pulled_to_5pct",
                                "symbol": sym,
                                "side": side,
                                "entry": str(entry),
                                "old_stop": str(stop),
                                "new_stop": str(new_stop),
                                "price_at_trigger": str(mark) if mark is not None else None,
                                "pull_pct": stop_pull_pct(),
                                "ts": time.time(),
                            })
                        except Exception:
                            pass
                    try:
                        append_journal({
                            "event": "STOP_UPDATE",
                            "symbol": sym,
                            "side": side,
                            "stop_before": str(ref_stop),
                            "stop_after": str(new_stop),
                            "source": "exchange" if exch_stop is not None else "plan",
                            "ok": True,
                            "action": act_name,
                            "ts": time.time(),
                        })
                    except Exception:
                        pass
                    try:
                        record_stop_change(
                            symbol=sym, stop_price=str(new_stop),
                            source=act.get("action"), reason="exit_engine", side=side,
                        )
                    except Exception:
                        pass
                elif act_name in ("mae_cut", "regime_exit", "max_hold", "vol_expansion"):
                    if getattr(broker, "_halted", False):
                        try:
                            append_journal({
                                "event": "exit_apply_under_halt",
                                "symbol": sym,
                                "side": side,
                                "action": act_name,
                                "halt_reason": getattr(broker, "_halt_reason", None),
                                "ts": time.time(),
                            })
                        except Exception:
                            pass
                    try:
                        await broker.reduce_position_qty(
                            sym, side, Decimal(str(getattr(pos, "quantity", 0) or 0)),
                        )
                    except Exception as cexc:
                        logger.warning("exit_engine close %s: %s", sym, type(cexc).__name__)
        except Exception as exc:
            logger.warning("run_exit_pass %s: %s", sym, type(exc).__name__)
    try:
        if path is not None:
            save_positions(plans, path)  # peaks + current_regime
        else:
            save_positions(plans)
    except Exception as sexc:
        logger.warning("run_exit_pass save_positions: %s", type(sexc).__name__)
    return {"mode": mode, "actions": n, "migrated": len(migrated)}
