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
MAX_OPEN_SAME_SIDE = 3  # Q2 arbiter: ≤3 positions of one sign
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
    try:
        COOLDOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
        now = time.time() * 1000
        pruned = {k: v for k, v in _COOLDOWN.items() if v > now}
        COOLDOWN_PATH.write_text(json.dumps(pruned), encoding="utf-8")
        _COOLDOWN.clear()
        _COOLDOWN.update(pruned)
    except Exception as exc:
        logger.warning("cooldown save: %s", type(exc).__name__)


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
    return LiveEntryPlan(
        symbol=cand.symbol,
        side=cand.side.lower(),
        quantity=qty,
        stop_price=cand.stop,
        take_profit_price=cand.take_profit,
        leverage=lev,
        client_tag="arm",
        entry_price=cand.entry,
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
