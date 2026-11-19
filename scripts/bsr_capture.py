"""Run the read-only BSR BingX and Binance market-data recorders."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from astra_bot.research.bsr.binance_spot_recorder import BinanceSpotRecorder
from astra_bot.research.bsr.websocket_recorder import BingXMarketRecorder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=[
        "BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT",
    ])
    parser.add_argument("--output", type=Path, default=Path("data/bsr/bingx_market.jsonl"))
    args = parser.parse_args()
    spot_output = args.output.with_name(
        args.output.stem + "_binance_spot" + args.output.suffix
    )

    async def capture() -> None:
        await asyncio.gather(
            BingXMarketRecorder(args.symbols, args.output).run_forever(),
            BinanceSpotRecorder(args.symbols, spot_output).run_forever(),
        )

    asyncio.run(capture())


if __name__ == "__main__":
    main()
