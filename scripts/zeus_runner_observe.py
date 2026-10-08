"""Zeus paper-clock split 4/9: 4h structure observe (wedge + channel + sweeps)."""

from __future__ import annotations

from zeus_runner_common import (
    BingXClient,
    ZeusChannelBoundaryStrategy,
    ZeusTradeLog,
    ZeusWedgeRetestStrategy,
    logger,
)
from zeus_runner_ltf import observe_ltf_impulse


async def observe_zeus(
    *,
    bingx: BingXClient,
    zeus: ZeusWedgeRetestStrategy,
    journal: ZeusTradeLog,
    symbol: str,
    zeus_channel: ZeusChannelBoundaryStrategy | None = None,
) -> None:
    try:
        klines = await bingx.get_candles(symbol, "4h", limit=80)
        closed_4h = list(klines) if klines else []
        if len(closed_4h) >= 2:
            closed_4h = closed_4h[:-1]
        diag = zeus.diagnose(closed_4h)
        journal.structure_state(symbol=symbol, snapshot=diag)
        if not diag.get("would_signal") and diag.get("reject_reason"):
            journal.reject(
                symbol=symbol,
                reason=str(diag.get("reject_reason") or ""),
                stage=str(diag.get("stage") or ""),
                snapshot=diag,
            )
        if zeus_channel is not None and getattr(
            getattr(zeus_channel, "config", None), "enabled", False
        ):
            diag_ch = zeus_channel.diagnose(closed_4h)
            snap = dict(diag_ch)
            snap["structure"] = "channel"
            journal.structure_state(symbol=symbol, snapshot=snap)
            if not diag_ch.get("would_signal") and diag_ch.get("reject_reason"):
                journal.reject(
                    symbol=symbol,
                    reason="channel:" + str(diag_ch.get("reject_reason") or ""),
                    stage=str(diag_ch.get("stage") or ""),
                    snapshot=snap,
                )
        try:
            import sys as _sys
            from pathlib import Path as _P

            _scripts = str(_P(__file__).resolve().parent)
            if _scripts not in _sys.path:
                _sys.path.insert(0, _scripts)
            from zeus_liquidity_shadow import log_liquidity_sweep

            log_liquidity_sweep(journal=journal, symbol=symbol, bars=closed_4h)
        except Exception as _sw_exc:
            logger.debug("liquidity_sweep: %s", _sw_exc)
        await observe_ltf_impulse(bingx=bingx, journal=journal, symbol=symbol)

        try:
            import sys as _sys2
            from pathlib import Path as _P2
            _scripts2 = str(_P2(__file__).resolve().parent)
            if _scripts2 not in _sys2.path:
                _sys2.path.insert(0, _scripts2)
            from zeus_wedge_1h_shadow import observe_wedge_1h_shadow
            await observe_wedge_1h_shadow(bingx=bingx, symbol=symbol)  # dual-lb 48+72
        except Exception as _w1h_exc:
            logger.debug("wedge_1h_shadow: %s", _w1h_exc)

    except Exception as exc:
        logger.warning("observe_zeus skipped: %s", exp)
