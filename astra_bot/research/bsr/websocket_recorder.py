"""BingX USDT-M WebSocket recorder with explicit depth synchronization."""
from __future__ import annotations

import gzip
import json
import time
import uuid
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp


@dataclass(slots=True)
class DepthBookState:
    """Local book; valid only after action=all on the active connection."""

    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    synchronized: bool = False
    last_update_id: int | None = None

    def invalidate(self) -> None:
        self.bids.clear()
        self.asks.clear()
        self.synchronized = False
        self.last_update_id = None

    def apply(self, payload: dict[str, Any]) -> bool:
        action = payload.get("action")
        if action not in {"all", "update"}:
            self.invalidate()
            return False
        try:
            bids = payload.get("bids", [])
            asks = payload.get("asks", [])
            if action == "all":
                new_bids = {float(p): float(q) for p, q in bids if float(q) > 0}
                new_asks = {float(p): float(q) for p, q in asks if float(q) > 0}
                if not new_bids or not new_asks:
                    raise ValueError("empty snapshot")
                self.bids, self.asks = new_bids, new_asks
                self.synchronized = True
            elif not self.synchronized:
                return False
            else:
                self._apply_side(self.bids, bids)
                self._apply_side(self.asks, asks)
            if not self.bids or not self.asks or max(self.bids) >= min(self.asks):
                raise ValueError("invalid/crossed local book")
            update_id = payload.get("lastUpdateId")
            self.last_update_id = int(update_id) if update_id is not None else None
        except (TypeError, ValueError, KeyError):
            self.invalidate()
            return False
        return self.synchronized

    @staticmethod
    def _apply_side(side: dict[float, float], levels: list[list[str]]) -> None:
        for raw_price, raw_qty in levels:
            price, quantity = float(raw_price), float(raw_qty)
            if price <= 0 or quantity < 0:
                raise ValueError("invalid depth level")
            if quantity == 0:
                side.pop(price, None)
            else:
                side[price] = quantity


class BingXMarketRecorder:
    """Read-only raw recorder. It never touches account or order endpoints."""

    URL = "wss://open-api-swap.bingx.com/swap-market"

    def __init__(self, symbols: list[str], output: Path) -> None:
        if not symbols:
            raise ValueError("at least one symbol is required")
        self.symbols = symbols
        self.output = output
        self.books = {symbol: DepthBookState() for symbol in symbols}
        self._active_channels: set[str] = set()
        self._request_channels: dict[str, str] = {}

    @staticmethod
    def _decode_frame(data: bytes | str) -> str:
        if isinstance(data, str):
            return data
        try:
            return gzip.decompress(data).decode("utf-8")
        except (OSError, EOFError, zlib.error):
            return data.decode("utf-8")

    def _write(self, record: dict[str, Any]) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        with self.output.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")

    async def _handle(self, ws: aiohttp.ClientWebSocketResponse, raw: bytes | str) -> None:
        text = self._decode_frame(raw)
        if text == "Ping" or "ping" in text.lower():
            await ws.send_str("Pong")
            return
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            self._write({"venue": "bingx_swap", "kind": "unparsed", "received_at_ms": int(time.time() * 1000), "raw": text})
            return
        request_id = message.get("id")
        if request_id in self._request_channels and message.get("code") == 0:
            self._active_channels.add(self._request_channels[request_id])
        channel = message.get("dataType")
        payload = message.get("data")
        if not channel or channel not in self._active_channels or payload is None:
            return
        received_at_ms = int(time.time() * 1000)
        if channel.endswith("@incrDepth"):
            symbol = channel.split("@", 1)[0]
            state = self.books.get(symbol)
            if state is None:
                return
            synchronized = state.apply(payload)
            self._write({
                "venue": "bingx_swap", "kind": "depth", "symbol": symbol,
                "received_at_ms": received_at_ms, "synchronized": synchronized,
                "last_update_id": state.last_update_id, "payload": payload,
            })
            return
        if channel.endswith("@trade"):
            rows = payload if isinstance(payload, list) else [payload]
            for row in rows:
                self._write({
                    "venue": "bingx_swap", "kind": "trade",
                    "symbol": row.get("s"), "received_at_ms": received_at_ms,
                    "trade_id": str(row.get("t", "")),
                    "exchange_time_ms": int(row.get("T", 0)),
                    "price": row.get("p"), "quantity": row.get("q"),
                    "buyer_maker": row.get("m"),
                })
            return
        self._write({
            "venue": "bingx_swap", "kind": "market_event",
            "channel": channel, "received_at_ms": received_at_ms,
            "payload": payload,
        })

    async def run_once(self) -> None:
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=None)
        async with aiohttp.ClientSession(timeout=timeout) as session, session.ws_connect(
            self.URL, heartbeat=None, autoping=False, receive_timeout=None
        ) as ws:
            self._active_channels.clear()
            self._request_channels.clear()
            for symbol in self.symbols:
                for suffix in ("incrDepth", "trade", "markPrice"):
                    channel = f"{symbol}@{suffix}"
                    request_id = str(uuid.uuid4())
                    self._request_channels[request_id] = channel
                    await ws.send_str(json.dumps({
                        "id": request_id, "reqType": "sub", "dataType": channel,
                    }))
            async for message in ws:
                if message.type in {aiohttp.WSMsgType.BINARY, aiohttp.WSMsgType.TEXT}:
                    await self._handle(ws, message.data)
                elif message.type in {
                    aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.ERROR,
                }:
                    break
        for state in self.books.values():
            state.invalidate()

    async def run_forever(self) -> None:
        delay = 1.0
        while True:
            try:
                await self.run_once()
                delay = 1.0
            except (aiohttp.ClientError, TimeoutError, OSError) as exc:
                self._write({
                    "venue": "bingx_swap", "kind": "connection_error",
                    "received_at_ms": int(time.time() * 1000), "error": str(exc),
                })
            finally:
                for state in self.books.values():
                    state.invalidate()
            await asyncio.sleep(delay)
            delay = min(delay * 2.0, 30.0)
