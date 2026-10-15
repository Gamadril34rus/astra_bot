"""VOL_EXPANSION cooldown hooks for paper (Zeus runner) and live clock.

Does NOT patch trading_engine.py (transport limit). Applies MethodType wraps:
  paper: PaperBroker.open_position + PaperBroker._log_trade
  live:  LiveBroker.sync_closed_plans (set only on VOL exit)

Owner 15.10 / arena v3.
"""
from __future__ import annotations

import logging
import types
from typing import Any

logger = logging.getLogger(__name__)


def apply_paper_hooks(broker: Any) -> None:
    """Wrap paper broker: block open on cooldown; set on VOL_EXPANSION close."""
    from astra_bot.decision.reentry_guard import (
        check as vol_check,
        is_vol_expansion_reason,
        note_skip,
        set_cooldown,
    )

    orig_open = broker.open_position
    orig_log = broker._log_trade

    def open_with_vol_cd(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        symbol = kwargs.get("symbol") or (args[0] if args else None)
        direction = kwargs.get("direction") or (args[1] if len(args) > 1 else None)
        side = str(direction or "").lower()
        sym = str(symbol or "")
        if sym and side in ("long", "short"):
            try:
                blocked, until = vol_check(sym, side)
                if blocked:
                    logger.info(
                        "paper: skip %s %s — vol_cooldown until=%.0f",
                        sym,
                        side,
                        until or 0,
                    )
                    note_skip(sym, side, until)
                    return None
            except Exception as exc:
                logger.debug("paper vol_cd check: %s", type(exc).__name__)
        return orig_open(*args, **kwargs)

    def log_with_vol_cd(self, trade):  # type: ignore[no-untyped-def]
        try:
            er = getattr(trade, "exit_reason", "") or ""
            if is_vol_expansion_reason(er):
                side = str(getattr(trade, "direction", "") or "").lower()
                sym = str(getattr(trade, "symbol", "") or "")
                if sym and side in ("long", "short"):
                    closed_raw = getattr(trade, "closed_at", None)
                    try:
                        cts = float(closed_raw) / (
                            1000.0 if float(closed_raw) > 1e12 else 1.0
                        )
                    except Exception:
                        cts = None
                    set_cooldown(
                        sym,
                        side,
                        trade_id=str(getattr(trade, "id", "") or "") or None,
                        exit_reason=str(er),
                        closed_ts=cts,
                    )
        except Exception as exc:
            logger.debug("paper vol_cd set: %s", type(exc).__name__)
        return orig_log(trade)

    broker.open_position = types.MethodType(open_with_vol_cd, broker)
    broker._log_trade = types.MethodType(log_with_vol_cd, broker)
    logger.info("vol_cd paper hooks applied")


def apply_live_sync_hook(broker: Any) -> None:
    """Wrap LiveBroker.sync_closed_plans: set cooldown only on VOL_EXPANSION exit."""
    from astra_bot.decision.reentry_guard import (
        is_vol_expansion_reason,
        set_cooldown,
    )

    if getattr(broker, "_vol_cd_sync_hooked", False):
        return
    orig = broker.sync_closed_plans

    async def sync_with_vol_cd_v2(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        result = await orig(*args, **kwargs)
        try:
            from pathlib import Path
            import json
            import time

            p = Path("models/zeus_live_trades.jsonl")
            if not p.exists():
                return result
            lines = p.read_text(encoding="utf-8").splitlines()[-40:]
            now = time.time()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if str(row.get("event") or "") != "exit":
                    continue
                er = str(row.get("exit_reason") or row.get("reason") or "")
                if not is_vol_expansion_reason(er):
                    continue
                # only recent exits (last 2h) to avoid replaying history
                try:
                    ts = float(row.get("ts") or 0)
                    if ts > 1e12:
                        ts /= 1000.0
                except Exception:
                    ts = 0.0
                if ts and (now - ts) > 7200:
                    continue
                side = str(row.get("side") or "").lower()
                sym = str(row.get("symbol") or "")
                if not sym or side not in ("long", "short"):
                    continue
                set_cooldown(
                    sym,
                    side,
                    trade_id=str(row.get("trade_id") or row.get("signal_id") or "")
                    or None,
                    exit_reason=er,
                    closed_ts=ts or None,
                )
        except Exception as exc:
            logger.debug("live vol_cd trades scan: %s", type(exc).__name__)
        return result

    broker.sync_closed_plans = types.MethodType(sync_with_vol_cd_v2, broker)
    broker._vol_cd_sync_hooked = True
    logger.info("vol_cd live sync hook applied")
