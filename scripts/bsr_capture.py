"""Run the read-only BSR BingX market-data recorder."""
from __future__ import annotations
import argparse
import asyncio
from pathlib import Path

from astra_bot.research.bsr.websocket_recorder import BingXMarketRecorder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=[
        "BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT",
    ])
    parser.add_argument("--output", type=Path, default=Path("data/bsr/bingx_market.jsonl"))
    args = parser.parse_args()
    asyncio.run(BingXMarketRecorder(args.symbols, args.output).run_forever())


if __name__ == "__main__":
    main()
