"""
ASTRA BOT — BingX WebSocket client (public market data).

Минимальная реализация под интерфейс OKXWebSocket: используется в
``main.py`` как фоновая подписка на публичные каналы (kline/ticker).
Бумажный контур (TradingEngine) работает по REST и WebSocket не требует.

Публичный WS BingX: ``wss://open-api-ws.bingx.com/market``
Формат подписки: ``{"type": "subscribe", "dataType": "BTC-USDT@kline_1m"}``
"""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

BINGX_WS_BASE = "wss://open-api-ws.bingx.com/market"
# BingX отдаёт клиентам ping-сообщения — отвечаем pong тем же id.
BINGX_WS_PING_INTERVAL = 20


class BingXWebSocket:
    """BingX public market WebSocket (интерфейс совместим с OKXWebSocket)."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.sandbox = config.get("sandbox", False)
        self.base_url = config.get("base_url", "")
        # У BingX нет отдельного demo-WS для spot; sandbox-хост не используем.
        self.url = BINGX_WS_BASE

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._running = False
        self._reconnect_delay = 5
        self._max_reconnect_delay = 60
        # Сильные ссылки на фоновые задачи (GC не собирает задачи asyncio).
        self._bg_tasks: set[asyncio.Task] = set()

        # Callback'и (не используются активным контуром; интерфейс сохранён)
        self._callbacks: dict[str, list[Callable]] = {}
        self._subscriptions: dict[str, Any] = {}

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    def _spawn(self, coro) -> asyncio.Task:
        """Запустить фоновую задачу, удерживая на неё сильную ссылку."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    async def connect(self):
        """Подключиться к WebSocket."""
        try:
            self._session = aiohttp.ClientSession()
            self._ws = await self._session.ws_connect(self.url)
            self._running = True
            logger.info("BingX WebSocket connected: %s", self.url)
            self._spawn(self._message_handler())
            self._spawn(self._heartbeat())
        except Exception as exc:
            logger.error("BingX WebSocket connection failed: %s", exc)
            await self._reconnect()

    async def disconnect(self):
        """Отключиться от WebSocket."""
        self._running = False
        for task in list(self._bg_tasks):
            task.cancel()
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        self._bg_tasks.clear()

        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("BingX WebSocket disconnected")

    async def _reconnect(self):
        """Переподключение с экспоненциальной задержкой."""
        if not self._running:
            return
        delay = self._reconnect_delay
        logger.info("BingX WS reconnecting in %ss...", delay)
        await asyncio.sleep(delay)
        try:
            self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)
            await self.connect()
        except Exception as exc:
            logger.error("BingX WS reconnect failed: %s", exc)
            self._spawn(self._reconnect())

    async def _heartbeat(self):
        """Периодический ping, чтобы BingX не разрывал соединение."""
        try:
            while self._running and self._ws and not self._ws.closed:
                await asyncio.sleep(BINGX_WS_PING_INTERVAL)
                try:
                    await self._ws.send_str(json.dumps({"ping": int(asyncio.get_event_loop().time() * 1000)}))
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    async def _message_handler(self):
        """Обработчик входящих сообщений."""
        while self._running and self._ws and not self._ws.closed:
            try:
                msg = await self._ws.receive()
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    # Ответ на наш ping: {"pong": <ts>} — пропускаем.
                    if "pong" in data:
                        continue
                    await self._handle_message(data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error("BingX WS error: %s", self._ws.exception())
                    break
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                    logger.info("BingX WS closed")
                    break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("BingX WS message error: %s", exc)
                break

    async def _handle_message(self, data: dict[str, Any]):
        """Раздать сообщение по подпискам (для будущих потребителей)."""
        data_type = data.get("dataType") or ""
        for key, callbacks in self._callbacks.items():
            if key and (data_type.startswith(key.split(":")[0]) or key in data_type):
                for cb in callbacks:
                    try:
                        result = cb(data)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as exc:
                        logger.debug("BingX WS callback error: %s", exc)

    # --------------------------------------------------------- подписки

    def _data_type(self, channel: str, symbol: str) -> str:
        """Сопоставить канал OKX-стиля с dataType BingX."""
        sym = symbol.replace("/", "-")
        mapping = {
            "candles": lambda: f"{sym}@kline_1m",
            "orderbook": lambda: f"{sym}@depth20",
            "trades": lambda: f"{sym}@trade",
            "ticker": lambda: f"{sym}@ticker",
        }
        fn = mapping.get(channel)
        if fn is None:
            raise ValueError(f"Unsupported BingX WS channel: {channel}")
        return fn()

    async def subscribe(self, channel: str, symbol: str, **args) -> None:
        """Подписаться на канал (kline_1m, depth20, trade, ticker)."""
        if not self.is_connected:
            logger.warning("Cannot subscribe: BingX WS not connected")
            return
        data_type = self._data_type(channel, symbol)
        try:
            await self._ws.send_str(json.dumps({"type": "subscribe", "dataType": data_type}))
            self._subscriptions[f"{channel}:{symbol}"] = data_type
            logger.debug("BingX WS subscribed: %s", data_type)
        except Exception as exc:
            logger.error("BingX WS subscribe error: %s", exc)

    async def unsubscribe(self, channel: str, symbol: str) -> None:
        """Отписаться от канала."""
        if not self.is_connected:
            return
        try:
            data_type = self._data_type(channel, symbol)
            await self._ws.send_str(json.dumps({"type": "unsubscribe", "dataType": data_type}))
            self._subscriptions.pop(f"{channel}:{symbol}", None)
        except Exception as exc:
            logger.error("BingX WS unsubscribe error: %s", exc)

    def on_candles(self, symbol: str):
        """Декоратор регистрации callback'а на свечи."""
        def decorator(func):
            self._callbacks.setdefault(f"candles:{symbol}", []).append(func)
            return func
        return decorator

    def on_orderbook(self, symbol: str):
        def decorator(func):
            self._callbacks.setdefault(f"orderbook:{symbol}", []).append(func)
            return func
        return decorator

    def on_trades(self, symbol: str):
        def decorator(func):
            self._callbacks.setdefault(f"trades:{symbol}", []).append(func)
            return func
        return decorator

    def on_ticker(self, symbol: str):
        def decorator(func):
            self._callbacks.setdefault(f"ticker:{symbol}", []).append(func)
            return func
        return decorator

    async def start(self):
        """Запустить WebSocket."""
        await self.connect()

    async def run_forever(self):
        """Бесконечный цикл (совместимость интерфейса)."""
        await self.connect()
        while self._running:
            await asyncio.sleep(1)


# Фабрика для создания WebSocket клиента
def create_bingx_websocket(config: dict[str, Any]) -> BingXWebSocket:
    """Создать BingX WebSocket клиент."""
    return BingXWebSocket(config)
