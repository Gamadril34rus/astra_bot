"""
ASTRA BOT — BingX WebSocket client (USDT-M perpetual market data).

Минимальная реализация под интерфейс OKXWebSocket: используется в
``main.py`` как фоновая подписка на публичные каналы (kline/ticker).
Бумажный контур (TradingEngine) работает по REST и WebSocket не требует.

Публичный swap-WS BingX: ``wss://open-api-swap.bingx.com/swap-market``
Формат подписки: ``{"id": "<uuid>", "reqType": "sub",
"dataType": "BTC-USDT@kline_1m"}``
Сообщения приходят в gzip; сервер шлёт текстовый ``Ping`` — отвечаем
``Pong``.
"""

import asyncio
import gzip
import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

BINGX_WS_BASE = "wss://open-api-swap.bingx.com/swap-market"
# Периодический ping, чтобы BingX не разрывал соединение.
BINGX_WS_PING_INTERVAL = 20


class BingXWebSocket:
    """BingX swap public market WebSocket (интерфейс совместим с OKXWebSocket)."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.sandbox = config.get("sandbox", False)
        self.base_url = config.get("base_url", "")
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
                    await self._ws.send_str("Ping")
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _decode_payload(raw: bytes | str) -> dict[str, Any] | str | None:
        """Распаковать сообщение swap-WS: gzip-байты или текст.

        Возвращает dict (данные), строку "Ping" (нужен "Pong") или None.
        """
        try:
            if isinstance(raw, (bytes, bytearray)):
                try:
                    text = gzip.decompress(bytes(raw)).decode("utf-8")
                except OSError:
                    text = bytes(raw).decode("utf-8", errors="ignore")
            else:
                text = raw
            text = text.strip()
            if text == "Ping":
                return "Ping"
            if text == "Pong":
                return None
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    async def _message_handler(self):
        """Обработчик входящих сообщений."""
        while self._running and self._ws and not self._ws.closed:
            try:
                msg = await self._ws.receive()
                if msg.type == aiohttp.WSMsgType.TEXT:
                    decoded = self._decode_payload(msg.data)
                    if decoded == "Ping":
                        try:
                            await self._ws.send_str("Pong")
                        except Exception:
                            break
                        continue
                    if isinstance(decoded, dict):
                        # Ответ на наш ping: {"pong": <ts>} — пропускаем.
                        if "pong" in decoded:
                            continue
                        await self._handle_message(decoded)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    decoded = self._decode_payload(msg.data)
                    if decoded == "Ping":
                        try:
                            await self._ws.send_str("Pong")
                        except Exception:
                            break
                        continue
                    if isinstance(decoded, dict):
                        await self._handle_message(decoded)
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

    def _data_type(self, channel: str, symbol: str, **args: Any) -> str:
        """Сопоставить канал OKX-стиля с dataType swap-WS BingX."""
        sym = symbol.replace("/", "-")
        interval = str(args.get("interval", "1m"))
        mapping = {
            "candles": lambda: f"{sym}@kline_{interval}",
            "orderbook": lambda: f"{sym}@depth20",
            "trades": lambda: f"{sym}@trade",
            "ticker": lambda: f"{sym}@ticker",
            "last_price": lambda: f"{sym}@lastPrice",
        }
        fn = mapping.get(channel)
        if fn is None:
            raise ValueError(f"Unsupported BingX WS channel: {channel}")
        return fn()

    def _sub_message(self, channel: str, symbol: str, **args: Any) -> str:
        """Сообщение подписки формата swap-WS."""
        return json.dumps({
            "id": str(uuid.uuid4()),
            "reqType": "sub",
            "dataType": self._data_type(channel, symbol, **args),
        })

    async def subscribe(self, channel: str, symbol: str, **args) -> None:
        """Подписаться на канал (kline, depth20, trade, ticker, lastPrice)."""
        if not self.is_connected:
            logger.warning("Cannot subscribe: BingX WS not connected")
            return
        try:
            await self._ws.send_str(self._sub_message(channel, symbol, **args))
            self._subscriptions[f"{channel}:{symbol}"] = self._data_type(
                channel, symbol, **args
            )
            logger.debug(
                "BingX WS subscribed: %s", self._subscriptions[f"{channel}:{symbol}"]
            )
        except Exception as exc:
            logger.error("BingX WS subscribe error: %s", exc)

    async def unsubscribe(self, channel: str, symbol: str) -> None:
        """Отписаться от канала."""
        if not self.is_connected:
            return
        try:
            data_type = self._data_type(channel, symbol)
            await self._ws.send_str(json.dumps({
                "id": str(uuid.uuid4()),
                "reqType": "unsub",
                "dataType": data_type,
            }))
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
