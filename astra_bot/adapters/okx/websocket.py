"""
ASTRA BOT — OKX WebSocket Client
"""

import asyncio
import json
import logging
import time
from typing import Optional, Callable, Dict, List, Any
from datetime import datetime

import aiohttp

from ..base import (
    Candle,
    OrderBook,
    OrderBookEntry,
    Trade,
)

logger = logging.getLogger(__name__)


class OKXWebSocket:
    """
    OKX WebSocket клиент для получения рыночных данных в реальном времени.
    
    Документация: https://www.okx.com/docs-v5/#websocket-public
    """
    
    WEBSOCKET_URL = "wss://ws.okx.com:8443/ws/v5/public"
    WEBSOCKET_URL_SANDBOX = "wss://ws.okx.com:8443/ws/v5/public"
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.sandbox = config.get("sandbox", False)
        self.base_url = config.get("base_url", "")
        
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._reconnect_delay = 5
        self._max_reconnect_delay = 60
        
        # Callback'и
        self._callbacks: Dict[str, List[Callable]] = {}
        
        # Subscription state
        self._subscriptions: Dict[str, Any] = {}
        
        # Для подписки на каналы
        self._channels = {
            "candles": "candle1m",  # Таймфрейм можно менять
            "orderbook": "books",
            "trades": "trades",
            "ticker": "tickers",
        }
    
    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._ws.closed
    
    async def connect(self):
        """Подключиться к WebSocket"""
        url = self.WEBSOCKET_URL_SANDBOX if self.sandbox else self.WEBSOCKET_URL
        
        try:
            self._session = aiohttp.ClientSession()
            self._ws = await self._session.ws_connect(url)
            self._running = True
            
            logger.info(f"OKX WebSocket connected: {url}")
            
            # Запуск обработчика сообщений
            asyncio.create_task(self._message_handler())
            
        except Exception as e:
            logger.error(f"OKX WebSocket connection failed: {e}")
            await self._reconnect()
    
    async def disconnect(self):
        """Отключиться от WebSocket"""
        self._running = False
        
        if self._ws:
            await self._ws.close()
            self._ws = None
        
        if self._session:
            await self._session.close()
            self._session = None
        
        logger.info("OKX WebSocket disconnected")
    
    async def _reconnect(self):
        """Переподключение"""
        if not self._running:
            return
        
        delay = self._reconnect_delay
        logger.info(f"Reconnecting in {delay}s...")
        
        await asyncio.sleep(delay)
        
        try:
            self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)
            await self.connect()
        except Exception as e:
            logger.error(f"Reconnect failed: {e}")
            asyncio.create_task(self._reconnect())
    
    async def _message_handler(self):
        """Обработчик сообщений"""
        while self._running and self._ws and not self._ws.closed:
            try:
                msg = await self._ws.receive()
                
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._handle_message(data)
                
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {msg.data}")
                    await self._reconnect()
                    break
                
            except asyncio.CancelledError:
                break
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error: {e}")
            except Exception as e:
                logger.error(f"Message handler error: {e}")
                await self._reconnect()
                break
    
    async def _handle_message(self, data: Dict[str, Any]):
        """Обработать сообщение"""
        channel = data.get("arg", {}).get("channel", "")
        
        if channel in self._callbacks:
            for callback in self._callbacks[channel]:
                try:
                    await callback(data)
                except Exception as e:
                    logger.error(f"Callback error for channel {channel}: {e}")
    
    async def subscribe(
        self,
        channel: str,
        symbol: str,
        **args
    ):
        """Подписаться на канал"""
        if not self.is_connected:
            logger.warning("Cannot subscribe: WebSocket not connected")
            return
        
        subscription = {
            "op": "subscribe",
            "args": [{
                "channel": channel,
                "instId": symbol,
                **args
            }]
        }
        
        try:
            await self._ws.send_str(json.dumps(subscription))
            self._subscriptions[f"{channel}:{symbol}"] = subscription
            logger.debug(f"Subscribed to {channel}:{symbol}")
        except Exception as e:
            logger.error(f"Subscribe error: {e}")
    
    async def unsubscribe(
        self,
        channel: str,
        symbol: str
    ):
        """Отписаться от канала"""
        if not self.is_connected:
            return
        
        subscription = {
            "op": "unsubscribe",
            "args": [{
                "channel": channel,
                "instId": symbol
            }]
        }
        
        try:
            await self._ws.send_str(json.dumps(subscription))
            key = f"{channel}:{symbol}"
            self._subscriptions.pop(key, None)
            logger.debug(f"Unsubscribed from {channel}:{symbol}")
        except Exception as e:
            logger.error(f"Unsubscribe error: {e}")
    
    def on_candles(
        self,
        symbol: str,
        timeframe: str,
        callback: Callable[[List[Candle]], None]
    ):
        """Подписаться на свечи"""
        channel = f"candle{timeframe}"
        
        if channel not in self._callbacks:
            self._callbacks[channel] = []
        
        async def wrapper(data):
            candles_data = data.get("data", [])
            candles = []
            for item in candles_data:
                candle = Candle(
                    exchange="okx",
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time=int(item[0]),
                    open=float(item[1]),
                    high=float(item[2]),
                    low=float(item[3]),
                    close=float(item[4]),
                    volume=float(item[5]),
                    quote_volume=float(item[6]) if len(item) > 6 else 0,
                )
                candles.append(candle)
            
            if candles:
                callback(candles)
        
        self._callbacks[channel].append(wrapper)
        
        if self.is_connected:
            asyncio.create_task(
                self.subscribe(channel, symbol, "size", "10")
            )
    
    def on_orderbook(
        self,
        symbol: str,
        callback: Callable[[OrderBook], None]
    ):
        """Подписаться на стакан"""
        channel = "books"
        
        if channel not in self._callbacks:
            self._callbacks[channel] = []
        
        async def wrapper(data):
            books_data = data.get("data", [])
            if not books_data:
                return
            
            book_data = books_data[0]
            
            bids = []
            for bid in book_data.get("bids", [])[:20]:
                bids.append(OrderBookEntry(
                    price=float(bid[0]),
                    quantity=float(bid[1]),
                ))
            
            asks = []
            for ask in book_data.get("asks", [])[:20]:
                asks.append(OrderBookEntry(
                    price=float(ask[0]),
                    quantity=float(ask[1]),
                ))
            
            # Сортировка
            bids.sort(key=lambda x: x.price, reverse=True)
            asks.sort(key=lambda x: x.price)
            
            orderbook = OrderBook(
                symbol=symbol,
                exchange="okx",
                bids=bids,
                asks=asks,
            )
            
            callback(orderbook)
        
        self._callbacks[channel].append(wrapper)
        
        if self.is_connected:
            asyncio.create_task(
                self.subscribe(channel, symbol, "size", "20", "depth", "20")
            )
    
    def on_trades(
        self,
        symbol: str,
        callback: Callable[[List[Trade]], None]
    ):
        """Подписаться на торги"""
        channel = "trades"
        
        if channel not in self._callbacks:
            self._callbacks[channel] = []
        
        async def wrapper(data):
            trades_data = data.get("data", [])
            trades = []
            for item in trades_data:
                trade = Trade(
                    trade_id=item[0],
                    exchange="okx",
                    symbol=symbol,
                    price=float(item[1]),
                    quantity=float(item[2]),
                    side=item[3],  # "buy" или "sell"
                    timestamp=int(item[4]),
                )
                trades.append(trade)
            
            if trades:
                callback(trades)
        
        self._callbacks[channel].append(wrapper)
        
        if self.is_connected:
            asyncio.create_task(
                self.subscribe(channel, symbol)
            )
    
    def on_ticker(
        self,
        symbol: str,
        callback: Callable[[Dict[str, Any]], None]
    ):
        """Подписаться на тикер"""
        channel = "tickers"
        
        if channel not in self._callbacks:
            self._callbacks[channel] = []
        
        async def wrapper(data):
            tickers_data = data.get("data", [])
            if tickers_data:
                callback(tickers_data[0])
        
        self._callbacks[channel].append(wrapper)
        
        if self.is_connected:
            asyncio.create_task(
                self.subscribe(channel, symbol)
            )
    
    async def start(self):
        """Запустить WebSocket"""
        await self.connect()
    
    async def run_forever(self):
        """Бесконечный цикл"""
        await self.connect()
        
        while self._running:
            await asyncio.sleep(1)


# Фабрика для создания WebSocket клиента
def create_okx_websocket(config: Dict[str, Any]) -> OKXWebSocket:
    """Создать OKX WebSocket клиент"""
    return OKXWebSocket(config)
