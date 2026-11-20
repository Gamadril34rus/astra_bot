"""Read-only Binance Spot trade-stream recorder for cross-venue basis research."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import aiohttp


class BinanceSpotRecorder:
    """Persist public Binance Spot trades with exchange and receive timestamps."""

    URL = "wss://data-stream.binance.vision:443/stream"

    def __init__(self, symbols: list[str], output: Path) -> None:
        if not symbols:
            raise ValueError("at least one symbol is required")
        self.symbols = symbols
        self.output = output

    @staticmethod
    def _symbol_stream(symbol: str) -> str:
        normalized = symbol.replace("-", "").lower()
        if not normalized.endswith("usdt") or len(normalized) <= 4:
            raise ValueError(f"expected a USDT spot symbol, got {symbol!r}")
        return f"{normalized}@trade"

    def _write(self, record: dict[str, Any]) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        with self.output.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")

    async def run_once(self) -> None:
        streams = "/".join(self._symbol_stream(symbol) for symbol in self.symbols)
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=None)
        async with aiohttp.ClientSession(timeout=timeout) as session, session.ws_connect(
            f"{self.URL}?streams={streams}", heartbeat=20, receive_timeout=None
        ) as ws:
            async for message in ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    try:
                        envelope = json.loads(message.data)
                        trade = envelope["data"]
                        if trade.get("e") != "trade":
                            continue
                        self._write({
                            "venue": "binance_spot",
                            "kind": "trade",
                            "symbol": trade["s"],
                            "received_at_ms": int(time.time() * 1000),
                            "exchange_time_ms": int(trade["T"]),
                            "trade_id": str(trade["t"]),
                            "price": trade["p"],
                            "quantity": trade["q"],
                            "buyer_maker": trade["m"],
                        })
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                        self._write({
                            "venue": "binance_spot",
                            "kind": "parse_error",
                            "received_at_ms": int(time.time() * 1000),
                            "error": str(exc),
                            "raw": message.data,
                        })
                elif message.type in {
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.ERROR,
                }:
                    break

    async def run_forever(self) -> None:
        delay = 1.0
        while True:
            try:
                await self.run_once()
                delay = 1.0
            except (aiohttp.ClientError, TimeoutError, OSError) as exc:
                self._write({
                    "venue": "binance_spot",
                    "kind": "connection_error",
                    "received_at_ms": int(time.time() * 1000),
                    "error": str(exc),
                })
            await asyncio.sleep(delay)
            delay = min(delay * 2.0, 30.0)
