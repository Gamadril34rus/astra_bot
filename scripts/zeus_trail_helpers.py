"""Zeus trail + sanitize — isolated so runner stays small to patch."""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

logger = logging.getLogger("paper_zeus")


async def zeus_trail_open_positions(
    *,
    engine: Any,
    journal: Any,
    bingx: Any,
) -> None:
    """Late trail: BE at +2R MFE, lock 1.5R at +2.5R; sanitize inverted/tight."""
    positions = list(getattr(engine.broker, "positions", None) or [])
    for pos in positions:
        try:
            symbol = getattr(pos, "symbol", "") or ""
            direction = str(getattr(pos, "direction", "") or "").lower()
            entry = float(pos.entry_price)
            stop = float(pos.stop_loss)
            notes = getattr(pos, "notes", None) or {}
            if not isinstance(notes, dict):
                notes = {}
            risk = 0.0
            try:
                init = float(notes.get("initial_stop") or 0)
                if init > 0:
                    risk = abs(entry - init)
            except (TypeError, ValueError):
                pass
            if risk <= 0:
                try:
                    risk = float(getattr(pos, "risk_distance", 0) or 0)
                except (TypeError, ValueError):
                    risk = 0.0
            if risk <= 0:
                risk = abs(entry - stop)
            MIN_STOP_PCT = 0.008
            inverted = (direction == "long" and stop >= entry) or (
                direction == "short" and stop <= entry
            )
            too_tight = entry > 0 and abs(entry - stop) / entry < MIN_STOP_PCT * 0.5
            if inverted or too_tight:
                fixed = (
                    entry * (1.0 - MIN_STOP_PCT)
                    if direction == "long"
                    else entry * (1.0 + MIN_STOP_PCT)
                )
                old_s = stop
                pos.stop_loss = Decimal(str(fixed))
                try:
                    engine.broker.save()
                except Exception:
                    pass
                try:
                    journal.stop_adjust(
                        symbol=symbol,
                        direction=direction,
                        old_stop=str(old_s),
                        new_stop=str(fixed),
                        why=(
                            "sanitize_inverted_stop"
                            if inverted
                            else "sanitize_tight_stop"
                        ),
                    )
                except Exception:
                    pass
                logger.warning(
                    "Zeus sanitize %s %s %s -> %s", symbol, direction, old_s, fixed
                )
                stop = fixed
                if risk <= 0:
                    risk = abs(entry - fixed)
            if risk <= 0 or not symbol:
                continue
            mark = entry
            try:
                k15 = await bingx.get_candles(symbol, "15m", limit=3)
                if k15:
                    mark = float(k15[-1].close)
            except Exception:
                hi = getattr(pos, "highest_price", None)
                lo = getattr(pos, "lowest_price", None)
                if direction == "long" and hi is not None:
                    mark = float(hi)
                elif direction == "short" and lo is not None:
                    mark = float(lo)
            if direction == "long":
                mfe_r = (mark - entry) / risk
            else:
                mfe_r = (entry - mark) / risk
            new_stop = stop
            why = ""
            # Late trail (research): early BE@1R hurt PF. BE@2R, lock 1.5R @2.5R.
            if mfe_r >= 2.5:
                if direction == "long":
                    cand = entry + 1.5 * risk
                    if cand > stop:
                        new_stop = cand
                        why = f"trail_lock_1.5R mfe={mfe_r:.2f}"
                else:
                    cand = entry - 1.5 * risk
                    if cand < stop:
                        new_stop = cand
                        why = f"trail_lock_1.5R mfe={mfe_r:.2f}"
            elif mfe_r >= 2.0:
                buf = max(risk * 0.05, entry * 0.0005)
                if direction == "long":
                    cand = entry + buf
                    if cand > stop:
                        new_stop = cand
                        why = f"trail_be_2R mfe={mfe_r:.2f}"
                else:
                    cand = entry - buf
                    if cand < stop:
                        new_stop = cand
                        why = f"trail_be_2R mfe={mfe_r:.2f}"
            if direction == "long" and new_stop < stop:
                continue
            if direction == "short" and new_stop > stop:
                continue
            if why and abs(new_stop - stop) / max(entry, 1e-12) > 1e-8:
                old = stop
                pos.stop_loss = Decimal(str(new_stop))
                try:
                    engine.broker.save()
                except Exception:
                    pass
                try:
                    journal.stop_adjust(
                        symbol=symbol,
                        direction=direction,
                        old_stop=str(old),
                        new_stop=str(new_stop),
                        why=why,
                    )
                except Exception as exc:
                    logger.warning("trail journal: %s", exc)
                logger.info(
                    "Zeus trail %s %s stop %s -> %s (%s)",
                    symbol,
                    direction,
                    old,
                    new_stop,
                    why,
                )
        except Exception as exc:
            logger.debug("trail skip: %s", exc)
