"""Run the read-only BSR BingX and Binance market-data recorders."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from astra_bot.research.bsr.binance_spot_recorder import BinanceSpotRecorder
from astra_bot.research.bsr.clock_sync import PublicClockCalibrator
from astra_bot.research.bsr.websocket_recorder import BingXMarketRecorder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=[
        "BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT",
    ])
    parser.add_argument("--output", type=Path, default=Path("data/bsr/bingx_market.jsonl"))
    parser.add_argument(
        "--duration-seconds", type=float, default=None,
        help="Stop cleanly after this many seconds; omit to run until interrupted.",
    )
    parser.add_argument(
        "--clock-output", type=Path, default=None,
        help="Public server-time calibration JSONL; defaults beside --output.",
    )
    args = parser.parse_args()
    if args.duration_seconds is not None and args.duration_seconds <= 0:
        parser.error("--duration-seconds must be greater than zero")
    spot_output = args.output.with_name(
        args.output.stem + "_binance_spot" + args.output.suffix
    )
    clock_output = args.clock_output or args.output.with_name("clock_sync.jsonl")

    async def capture() -> None:
        tasks = [
            asyncio.create_task(BingXMarketRecorder(args.symbols, args.output).run_forever()),
            asyncio.create_task(BinanceSpotRecorder(args.symbols, spot_output).run_forever()),
            asyncio.create_task(PublicClockCalibrator(clock_output).run_forever()),
        ]
        try:
            if args.duration_seconds is None:
                await asyncio.gather(*tasks)
            else:
                done, pending = await asyncio.wait(
                    tasks, timeout=args.duration_seconds,
                    return_when=asyncio.FIRST_EXCEPTION,
                )
                for task in done:
                    task.result()
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(capture())


if __name__ == "__main__":
    main()
