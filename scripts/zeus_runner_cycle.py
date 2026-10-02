"""Zeus paper-clock split 8/9: one cycle + once/loop scheduler."""

from __future__ import annotations

import asyncio

from zeus_runner_common import Any, logger
from zeus_runner_core import _snap_positions
from zeus_runner_journal import sync_journal_from_broker, zeus_trail_open_positions
from zeus_runner_observe import observe_zeus

async def zeus_run_cycles(ctx: dict[str, Any]) -> None:
    args = ctx["args"]
    engine = ctx["engine"]
    journal = ctx["journal"]
    trades_path = ctx["trades_path"]
    bingx = ctx["bingx"]
    zeus = ctx["zeus"]
    zeus_channel = ctx["zeus_channel"]
    symbols = ctx["symbols"]
    stop = ctx["stop"]

    async def one_cycle() -> None:
        before = _snap_positions(engine)
        await engine.step()
        ctx["known_ids"] = sync_journal_from_broker(
            engine=engine,
            journal=journal,
            before=before,
            trades_path=trades_path,
            known_trade_ids=ctx["known_ids"],
        )
        try:
            await zeus_trail_open_positions(
                engine=engine, journal=journal, bingx=bingx
            )
        except Exception as trail_exc:
            logger.warning("zeus trail: %s", trail_exc)
        for sym in symbols:
            await observe_zeus(
                bingx=bingx,
                zeus=zeus,
                zeus_channel=zeus_channel,
                journal=journal,
                symbol=sym,
            )
        n_open = len(engine.broker.positions or [])
        journal._write(
            {
                "event": "tick",
                "symbol": ",".join(symbols),
                "strategy": "zeus_channel+wedge",
                "note": "cycle channel+wedge lev_conf",
                "open_positions": n_open,
            }
        )
    if args.once:
        await one_cycle()
        logger.info(
            "Zeus cycle done open=%d", len(engine.broker.positions or [])
        )
    else:
        while not stop.is_set():
            try:
                await one_cycle()
            except Exception as exc:
                logger.exception("cycle error: %s", exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=args.interval)
            except TimeoutError:
                pass
