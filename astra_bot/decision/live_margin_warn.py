"""F15/F15b: protective-order TG warn with 1h/symbol dedup (any error text)."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MARGIN_WARN_PATH = Path("models/zeus_protect_margin_warn.json")
_send_tg_cb: Callable[[str], Any] | None = None


def set_send_tg(cb: Callable[[str], Any] | None) -> None:
    """Clock installs live TG sender for the duration of a tick."""
    global _send_tg_cb
    _send_tg_cb = cb


def margin_reject_msg(err: str) -> bool:
    """Legacy filter — kept for callers; F15b warns on any protect failure."""
    m = (err or "").lower()
    return any(
        x in m
        for x in (
            "available",
            "insufficient",
            "margin",
            "order size must be less",
            "109400",
            "109401",
            "take profit price",
            "stop loss price",
            "should be lower",
            "should be greater",
        )
    )


def maybe_warn_margin_reject(
    symbol: str,
    err: str,
    send_tg: Callable[[str], Any] | None = None,
    *,
    any_error: bool = True,
) -> bool:
    """Send ⚠️ once per symbol per hour on protective-order reject.

    F15b: any_error=True (default) — price-past, margin, 1094xx, etc.
    """
    if not err:
        return False
    if not any_error and not margin_reject_msg(err):
        return False
    try:
        import time as _t

        state: dict = {}
        if MARGIN_WARN_PATH.exists():
            state = json.loads(MARGIN_WARN_PATH.read_text(encoding="utf-8") or "{}")
        now = _t.time()
        last = float(state.get(symbol) or 0)
        if now - last < 3600:
            return False
        state[str(symbol)] = now
        MARGIN_WARN_PATH.parent.mkdir(parents=True, exist_ok=True)
        MARGIN_WARN_PATH.write_text(json.dumps(state), encoding="utf-8")
        msg = f"⚠️ {symbol}: защита отклонена — {err}"
        cb = send_tg if send_tg is not None else _send_tg_cb
        if cb is not None:
            try:
                res = cb(msg)
                if hasattr(res, "__await__"):
                    try:
                        import asyncio

                        asyncio.get_running_loop().create_task(res)
                    except RuntimeError:
                        try:
                            import asyncio

                            asyncio.run(res)
                        except Exception as exc:
                            logger.warning("margin warn TG async failed: %s", type(exc).__name__)
            except Exception as exc:
                logger.warning("margin warn TG failed: %s", type(exc).__name__)
        return True
    except Exception:
        return False
