"""Zeus paper-clock split 9/9: amain assembly + main()."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys

from zeus_runner_boot import zeus_journal_boot
from zeus_runner_common import Any, BingXClient, logger, setup_logging, zeus_engine_config
from zeus_runner_core import parse_args, resolve_symbols
from zeus_runner_cycle import zeus_run_cycles
from zeus_runner_engine import zeus_build_pipeline_and_engine

async def amain(args: argparse.Namespace) -> int:
    setup_logging()
    symbols = resolve_symbols(args)
    logger.info("Zeus symbols: %s", ",".join(symbols))
    env = (os.environ.get("ENVIRONMENT") or "").strip().lower()
    paper_flag = (os.environ.get("PAPER_TRADING") or "").strip().lower()
    if env and env not in ("paper", "test", "dev"):
        logger.error("ABORT: ENVIRONMENT=%s", env)
        return 2
    if not env:
        os.environ["ENVIRONMENT"] = "paper"
    if paper_flag not in ("1", "true", "yes"):
        os.environ["PAPER_TRADING"] = "true"

    api_key = os.environ.get("BINGX_API_KEY", "")
    api_secret = os.environ.get("BINGX_API_SECRET", "")
    bingx = BingXClient(
        {
            "api_key": api_key,
            "api_secret": api_secret,
            "enabled": True,
            "rate_limit_qps": 5,
        }
    )
    await bingx.initialize()
    config = zeus_engine_config(args, symbols)
    engine, zeus, zeus_channel = zeus_build_pipeline_and_engine(
        args, symbols, config, bingx
    )
    journal, trades_path, known_ids = zeus_journal_boot(engine, args, symbols)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    ctx: dict[str, Any] = {
        "args": args,
        "engine": engine,
        "journal": journal,
        "trades_path": trades_path,
        "known_ids": known_ids,
        "bingx": bingx,
        "zeus": zeus,
        "zeus_channel": zeus_channel,
        "symbols": symbols,
        "stop": stop,
    }
    await zeus_run_cycles(ctx)

    await bingx.close()
    return 0

def main() -> None:
    args = parse_args()
    try:
        code = asyncio.run(amain(args))
    except KeyboardInterrupt:
        code = 0
    sys.exit(code or 0)


if __name__ == "__main__":
    main()
