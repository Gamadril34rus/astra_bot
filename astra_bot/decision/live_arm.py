"""ZEUS_ARM=1 signal→entry hook helpers (sizing + guards).

Without ZEUS_ARM=1 the live clock stays observation-only.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from astra_bot.decision.conf_risk_ladder import conf_risk_pct
from astra_bot.decision.exit_plan import PLAN_TP_FRACTIONS, clamp_take_rr
from astra_bot.decision.live_broker import LiveBroker, LiveEntryPlan
from astra_bot.decision.live_positions import (
    count_open,
    open_symbols,
)

logger = logging.getLogger(__name__)

CONF_RISK_LADDER = ((0.88, 3.0), (0.80, 2.0), (0.70, 1.5))

# A7 leverage ladder — byte-for-byte from trading_engine (do not "improve")
LEVERAGE_LADDER: tuple[tuple[float, float, int], ...] = (
    (0.95, 3.0, 100),
    (0.90, 2.5, 50),
    (0.85, 2.0, 20),
    (0.80, 1.6, 10),
    (0.70, 1.3, 5),
    (0.55, 1.1, 3),
)


def leverage_for(
    confidence: float,
    ev_r: float,
    entry_price: float,
    stop_loss: float,
    max_leverage: int,
    min_ev_r: float,
    maintenance_margin_pct: float = 0.005,
) -> int:
    """Плечо сделки: база 2x по EV, выше — только по уверенности. Copy of paper A7."""
    if max_leverage <= 1:
        return 1
    if ev_r < min_ev_r:
        return 1
    lev = 2
    for min_conf, min_ev, rung in LEVERAGE_LADDER:
        if confidence >= min_conf and ev_r >= min_ev:
            lev = max(lev, min(rung, max_leverage))
            break
    lev = min(lev, max_leverage)
    entry = float(entry_price)
    stop = float(stop_loss)
    if entry > 0:
        d = abs(entry - stop) / entry
        if d > 0:
            feasible = int(1.0 / (d + maintenance_margin_pct)) - 1
            if lev > feasible:
                lev = max(1, feasible)
    return max(1, lev)

MAX_OPEN = 5
# Soft same-side cluster (owner 14.10): 0–3 of one sign always OK;
# 4th/5th of same sign only if sum(unrealizedProfit) of that side >= 0.
SAME_SIDE_SOFT_CAP = 2
MAX_OPEN_SAME_SIDE = 2  # alias for soft threshold (compat)
# Owner 28.10 anti-cluster: hard max same-side open (in addition to soft upnl rule).
# Blocks N+1 when already N of that side open. MAX_OPEN=5 unchanged.
SAME_SIDE_CLUSTER_MAX = 3
COOLDOWN_MS = 20 * 60 * 1000  # strategy|symbol|side as paper
COOLDOWN_PATH = Path("models/zeus_live_cooldown.json")
_COOLDOWN: dict[str, float] = {}  # key -> unblock_ts_ms


def arm_enabled() -> bool:
    return (os.environ.get("ZEUS_ARM") or "").strip() in ("1", "true", "yes")


@dataclass
class SignalCandidate:
    symbol: str
    side: str  # long|short
    entry: Decimal
    stop: Decimal
    take_profit: Decimal | None = None
    confidence: float | None = None
    leverage: int = 5
    strategy: str = "zeus"
    step_size: Decimal = Decimal("0.001")
    prior_r: float | None = None  # P7: rank by expected move (owner 19.10)


def _cooldown_key(strategy: str, symbol: str, side: str) -> str:
    return f"{strategy}|{symbol}|{side.lower()}"


def _load_cooldown() -> None:
    global _COOLDOWN
    if not COOLDOWN_PATH.exists():
        return
    try:
        raw = json.loads(COOLDOWN_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            _COOLDOWN = {str(k): float(v) for k, v in raw.items()}
    except Exception:
        pass


def _save_cooldown() -> None:
    """Persist full map; expiry is checked at read time with caller now_ms.

    Do not prune by wall-clock here — tests inject synthetic now_ms.
    """
    try:
        COOLDOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
        COOLDOWN_PATH.write_text(json.dumps(_COOLDOWN), encoding="utf-8")
    except Exception as exp:
        logger.warning("cooldown save: %s", type(exp).__name__)


def mark_cooldown(strategy: str, symbol: str, side: str, now_ms: float | None = None) -> None:
    _load_cooldown()
    now = now_ms if now_ms is not None else time.time() * 1000
    _COOLDOWN[_cooldown_key(strategy, symbol, side)] = now + COOLDOWN_MS
    _save_cooldown()


def cooldown_blocks(strategy: str, symbol: str, side: str, now_ms: float | None = None) -> bool:
    _load_cooldown()
    now = now_ms if now_ms is not None else time.time() * 1000
    until = _COOLDOWN.get(_cooldown_key(strategy, symbol, side), 0)
    return now < until



def same_side_cluster_blocks(
    side: str,
    *,
    side_count: int,
    side_upnl: float | Decimal,
    hard_max: int | None = None,
) -> bool:
    """True → skip new entry on this side.

    Owner 28.10: HARD cap SAME_SIDE_CLUSTER_MAX (default 3) — never open 4th+
    of the same side regardless of unrealized PnL (anti-correlated pack).
    Soft rule (owner 14.10) remains for edge cases if hard_max disabled via
    hard_max=0: 4th/5th only when side_upnl < 0.
    Caller still respects MAX_OPEN=5 total.
    """
    s = str(side or "").lower()
    if s not in ("long", "short"):
        return False
    n = int(side_count)
    cap = SAME_SIDE_CLUSTER_MAX if hard_max is None else int(hard_max)
    # Hard anti-cluster: already at/above cap → block (entry would be cap+1)
    if cap > 0 and n >= cap:
        return True
    # Legacy soft rule ONLY when hard cap disabled (hard_max=0)
    if cap > 0:
        return False
    if n < SAME_SIDE_SOFT_CAP:
        return False
    try:
        upnl = float(side_upnl)
    except (TypeError, ValueError):
        upnl = 0.0
    if upnl < 0:
        return True
    return False


def quantize_qty(qty: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return qty
    n = (qty / step).to_integral_value(rounding=ROUND_DOWN)
    return n * step


def size_qty(
    risk_budget: Decimal,
    entry: Decimal,
    stop: Decimal,
    step_size: Decimal = Decimal("0.001"),
) -> Decimal:
    """qty = risk_budget / |entry−stop|, floored to stepSize."""
    dist = abs(entry - stop)
    if dist <= 0:
        return Decimal("0")
    raw = risk_budget / dist
    return quantize_qty(raw, step_size)


def conflict_blocks(symbol: str, positions_path: Path | None = None) -> bool:
    """Symbol already in zeus_live_positions.json → no new entry."""
    return symbol in open_symbols(positions_path) if positions_path else symbol in open_symbols()


async def plan_from_candidate(
    broker: LiveBroker,
    cand: SignalCandidate,
    *,
    positions_path: Path | None = None,
    exchange_symbols: set[str] | None = None,
    exchange_open_count: int | None = None,
) -> LiveEntryPlan | None:
    # Prefer exchange truth when provided (ephemeral runner — file is blind).
    if exchange_symbols is not None:
        if cand.symbol in exchange_symbols:
            logger.info("arm: skip %s — already open on exchange", cand.symbol)
            return None
    elif conflict_blocks(cand.symbol, positions_path):
        logger.info("arm: skip %s — already in live positions", cand.symbol)
        return None
    if cooldown_blocks(cand.strategy, cand.symbol, cand.side):
        logger.info("arm: skip %s — cooldown", cand.symbol)
        return None
    open_n = (
        exchange_open_count
        if exchange_open_count is not None
        else count_open(positions_path or Path("models/zeus_live_positions.json"))
    )
    if open_n >= MAX_OPEN:
        logger.info("arm: skip — max_open=%d", MAX_OPEN)
        return None
    tier = conf_risk_pct(cand.confidence, CONF_RISK_LADDER, Decimal("0.01"))
    # heat can cap risk to 1% (paper parity) — applied if governor active
    try:
        from astra_bot.decision.zeus_heat import DEFAULT_HEAT_PATH, ZeusHeatGovernor

        heat = ZeusHeatGovernor(state_path=DEFAULT_HEAT_PATH)
        if heat.is_heat_active():
            # A-003-live / README: heat → risk ×0.5 of conf tier (not fixed 1%)
            tier = (tier * Decimal("0.5")).quantize(Decimal("0.0001"))
            logger.info("arm: heat_active → risk_pct=%s (tier×0.5)", tier)
    except Exception:
        pass
    budget = await broker.risk_budget(tier)
    step = cand.step_size
    if step is None or step <= 0:
        logger.error(
            "arm: REJECT %s step_size missing/invalid=%s — qty=0 (A-014)",
            cand.symbol,
            step,
        )
        return None
    qty = size_qty(budget, cand.entry, cand.stop, step)
    if qty <= 0:
        logger.error(
            "arm: REJECT %s sized qty=0 budget=%s entry=%s stop=%s step=%s",
            cand.symbol,
            budget,
            cand.entry,
            cand.stop,
            step,
        )
        return None
    # A7 leverage ladder byte-for-byte from paper (trading_engine.leverage_for)
    conf = float(cand.confidence or 0.0)
    dist = abs(float(cand.entry) - float(cand.stop))
    reward = abs(float(cand.take_profit) - float(cand.entry)) if cand.take_profit else dist
    ev_r = (reward / dist) if dist > 0 else 0.0  # R:R proxy when no model EV
    lev = leverage_for(
        confidence=conf,
        ev_r=ev_r,
        entry_price=float(cand.entry),
        stop_loss=float(cand.stop),
        max_leverage=int(broker.config.max_leverage),
        min_ev_r=1.0,
    )
    lev = min(int(lev), int(broker.config.max_leverage))
    # Live ENV override (owner: risk/qty unchanged, only margin). Cap by max_leverage (A7≤10).
    force_lev = (os.environ.get("ZEUS_LIVE_FORCE_LEVERAGE") or "").strip()
    if force_lev:
        try:
            lev = min(int(force_lev), int(broker.config.max_leverage))
        except ValueError:
            pass
    # Early TP1 lock (30.10): typical live MFE band ~0.5–2% of price ≈ 0.6–1.0R.
    # F15 put the *exchange* TP leg at clamp 2.0–2.3R → partial almost never filled,
    # runners gave back the path. Exchange TP1 = ZEUS_TP1_RR (default 0.6)×R;
    # tail clamp kept only as metadata (tp_tail) for parity/telemetry.
    entry_d = Decimal(str(cand.entry))
    stop_d = Decimal(str(cand.stop))
    r_abs = abs(entry_d - stop_d)
    side_l = cand.side.lower()
    try:
        tp1_rr = Decimal(str(os.environ.get("ZEUS_TP1_RR", "0.6") or "0.6"))
        if tp1_rr <= 0 or tp1_rr > 3:
            tp1_rr = Decimal("0.6")
    except Exception:
        tp1_rr = Decimal("0.6")
    raw_tp = cand.take_profit
    if raw_tp is None and r_abs > 0:
        raw_tp = entry_d + r_abs * Decimal("2.0") if side_l == "long" else entry_d - r_abs * Decimal("2.0")
    # tail at clamp kept for log/telemetry only (not exchange TP1 price)
    if raw_tp is not None and r_abs > 0:
        _tp_tail = clamp_take_rr(entry_d, stop_d, Decimal(str(raw_tp)), side_l, 2.0, 2.3)
        logger.debug("arm tp1_rr early=%s tail_clamp=%s", tp1_rr, _tp_tail)
    if r_abs > 0:
        if side_l == "long":
            tp1 = entry_d + r_abs * tp1_rr
        else:
            tp1 = entry_d - r_abs * tp1_rr
    else:
        tp1 = cand.take_profit  # fail-open
    tp_frac = float(PLAN_TP_FRACTIONS[0]) if PLAN_TP_FRACTIONS else 0.3
    try:
        heat_on = False
        try:
            from astra_bot.decision.zeus_heat import DEFAULT_HEAT_PATH, ZeusHeatGovernor

            heat_on = ZeusHeatGovernor(state_path=DEFAULT_HEAT_PATH).is_heat_active()
        except Exception:
            heat_on = False
        logger.info(
            "arm size %s conf=%s tier=%s budget=%s qty=%s lev=%s heat_active=%s",
            cand.symbol,
            conf,
            tier,
            budget,
            qty,
            lev,
            heat_on,
        )
    except Exception:
        pass
    # F15b: initial_stop = risk stop at entry; never overwritten by BE moves
    return LiveEntryPlan(
        symbol=cand.symbol,
        side=side_l,
        quantity=qty,
        stop_price=cand.stop,
        take_profit_price=tp1,
        leverage=lev,
        client_tag="arm",
        entry_price=cand.entry,
        tp_frac=tp_frac,
        initial_stop=stop_d,
    )


def format_entry_tg(
    symbol: str,
    side: str,
    qty: Decimal,
    entry: Decimal,
    stop: Decimal,
    tp: Decimal | None,
    *,
    vst: bool = True,
) -> str:
    side_ru = "ЛОНГ" if side.lower() == "long" else "ШОРТ"
    tp_s = str(tp) if tp is not None else "—"
    mode = "VST" if vst else "MAIN"
    return f"🚀 {symbol} {side_ru} {qty} @ {entry}; стоп {stop}; тейк {tp_s} ({mode})"


def rank_candidates(cands: list[SignalCandidate]) -> list[SignalCandidate]:
    """P7: sort by prior_r desc, tie-break confidence desc. None prior_r last.

    getattr: mocks/SimpleNamespace may omit prior_r (tests, partial rows).
    """
    def key(c: SignalCandidate):
        pr = getattr(c, "prior_r", None)
        conf = getattr(c, "confidence", None)
        return (
            pr if pr is not None else -1e9,
            conf if conf is not None else -1e9,
        )
    return sorted(cands, key=key, reverse=True)
