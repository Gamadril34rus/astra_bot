"""Audit immutable BSR BingX, Binance and public clock calibration captures."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from astra_bot.research.bsr.capture_audit import audit_capture_files
from astra_bot.research.bsr.clock_sync import summarize_clock_sync


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bingx",
        type=Path,
        default=Path("data/bsr/bingx_market.jsonl"),
        help="BingX Swap JSONL capture",
    )
    parser.add_argument(
        "--binance",
        type=Path,
        default=Path("data/bsr/bingx_market_binance_spot.jsonl"),
        help="Binance Spot JSONL capture",
    )
    parser.add_argument(
        "--clock",
        type=Path,
        default=Path("data/bsr/clock_sync.jsonl"),
        help="Public server clock calibration JSONL",
    )
    parser.add_argument("--output", type=Path, help="optional report JSON path")
    parser.add_argument("--max-latency-ms", type=int, default=5_000)
    args = parser.parse_args()

    report = audit_capture_files(
        [args.bingx, args.binance],
        max_latency_ms=args.max_latency_ms,
    )
    report["clock_sync"] = summarize_clock_sync(args.clock)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
