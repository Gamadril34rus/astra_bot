"""
ASTRA BOT — OKX REST API Client
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import aiohttp

from ...core.exceptions import ExchangeError
from ...core.metrics import HTTP_REQUEST_LATENCY, HTTP_REQUESTS_TOTAL
from ..base import (
    AccountBalance,
    Candle,
    ExchangeAdapter,
    ExchangeHealth,
    ExchangeHealthStatus,
    Instrument,
    Order,
    OrderBook,
    OrderBookEntry,
    Position,
    Trade,
)

logger = logging.getLogger(__name__)

# OKX API endpoints
OKX_API_BASE = "https://www.okx.com"
# OKX demo trading endpoint (отдельный хост, см. документацию OKX V5)
OKX_API_SANDBOX = "https://www.okx.com"
OKX_WS_BASE = "wss://ws.okx.com:8443/ws/v5/public"
OKX_WS_SANDBOX = "wss://wspap.okx.com:8443/ws/v5/public?brokerId=9999"

OKX_ENDPOINTS = {
    "spot": {
        "instruments": "/api/v5/public/instruments",
        "candles": "/api/v5/market/history-candles",
        "ticker": "/api/v5/market/ticker",
        "orderbook": "/api/v5/market/books",
        "trades": "/api/v5/market/trades",
        "account": "/api/v5/account/balance",
        "orders": "/api/v5/account/balance",  # Placeholder
        "place_order": "/api/v5/trade/orders",
        "cancel_order": "/api/v5/trade/cancel-order",
        "get_order": "/api/v5/trade/order",
        "open_orders": "/api/v5/account/orders",
    }
}


class OKXClient(ExchangeAdapter):
    """
    OKX Exchange REST API Client.

    Документация: https://www.okx.com/docs-v5/
    """

    exchange_name = "okx"
    exchange_type = "okx"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)

        self.api_key = config.get("api_key", "")
        self.api_secret = config.get("api_secret", "")
        self.passphrase = config.get("passphrase", "")
        self.sandbox = config.get("sandbox", False)
        self.base_url = config.get("base_url", OKX_API_BASE)
        self.enabled = config.get("enabled", True)
        self.contract_type = config.get("contract_type", "spot")

        self._session: aiohttp.ClientSession | None = None
        self._rate_limit_remaining = 100
        self._rate_limit_reset = 0.0

        # Client-side rate limiting (token bucket). OKX позволяет 20 req/2s
        # для публичных эндпоинтов и отдельные лимиты для торговых; по
        # умолчанию придерживаемся консервативных 10 req/s, значение можно
        # переопределить через config.
        self._rate_limit_qps = float(config.get("rate_limit_qps", 10))
        self._rate_bucket = self._rate_limit_qps
        self._rate_last = 0.0
        self._rate_lock = asyncio.Lock()

        # Кэш инструментов
        self._instrument_cache: dict[str, Instrument] = {}

    async def _acquire_rate_token(self) -> None:
        """Забрать один токен из token bucket, при необходимости подождав."""
        if self._rate_limit_qps <= 0:
            return
        async with self._rate_lock:
            now = asyncio.get_event_loop().time()
            if self._rate_last == 0.0:
                self._rate_last = now
            elapsed = now - self._rate_last
            self._rate_last = now
            self._rate_bucket = min(
                self._rate_limit_qps,
                self._rate_bucket + elapsed * self._rate_limit_qps,
            )
            if self._rate_bucket < 1.0:
                wait = (1.0 - self._rate_bucket) / self._rate_limit_qps
                await asyncio.sleep(wait)
                self._rate_bucket = 0.0
            else:
                self._rate_bucket -= 1.0

    async def initialize(self):
        """Инициализация клиента"""
        headers = {
            "Content-Type": "application/json",
        }
        if self.sandbox:
            # Demo-trading shares the www.okx.com host but requires this
            # header on every authenticated call.
            headers["x-simulated-trading"] = "1"
        self._session = aiohttp.ClientSession(base_url=self.base_url, headers=headers)
        logger.info(f"OKX client initialized, sandbox={self.sandbox}")

    async def close(self):
        """Закрытие клиента"""
        if self._session:
            await self._session.close()
            self._session = None

    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        """OKX HMAC SHA256 signature, base64-encoded."""
        message = f"{timestamp}{method.upper()}{request_path}{body}".encode()
        digest = hmac.new(self.api_secret.encode(), message, hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    def _prepare_auth_headers(self, method: str, request_path: str, body: str = "") -> dict[str, str]:
        """Подготовить авторизованные заголовки.

        OKX ожидает OK-ACCESS-TIMESTAMP в формате ISO 8601 с миллисекундами
        (50102 Timestamp request expired при unix-ms), а OK-ACCESS-SIGN —
        base64 от HMAC-SHA256, а не hex (50113 Invalid Sign).
        """
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"

        if self.api_key:
            return {
                "OK-ACCESS-KEY": self.api_key,
                "OK-ACCESS-SIGN": self._sign(timestamp, method, request_path, body),
                "OK-ACCESS-TIMESTAMP": timestamp,
                "OK-ACCESS-PASSPHRASE": self.passphrase,
                "Content-Type": "application/json",
            }
        return {"Content-Type": "application/json"}

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        body: dict | None = None,
        signed: bool = False
    ) -> dict[str, Any]:
        """Отправить запрос к API"""
        # Build query string once so it can be included in the signature prehash.
        query = ""
        if params:
            query = "?" + urlencode(params)
        url = f"{self.base_url}{endpoint}{query}"
        headers = {}

        if signed:
            body_str = json.dumps(body) if body else ""
            headers = self._prepare_auth_headers(method, endpoint + query, body_str)
        else:
            headers = {"Content-Type": "application/json"}

        if self._session is None:
            raise RuntimeError("OKXClient is not initialized; call initialize() first")

        await self._acquire_rate_token()
        try:
            start_time = time.time()
            http_status = 0

            if method == "GET":
                async with self._session.get(url, headers=headers) as resp:
                    http_status = resp.status
                    latency_s = time.time() - start_time
                    self._last_latency = latency_s * 1000
                    data = await resp.json()
            elif method == "POST":
                async with self._session.post(url, json=body, headers=headers) as resp:
                    http_status = resp.status
                    latency_s = time.time() - start_time
                    self._last_latency = latency_s * 1000
                    data = await resp.json()
            else:
                raise ValueError(f"Unsupported method: {method}")

            HTTP_REQUEST_LATENCY.labels(
                service="okx", endpoint=endpoint
            ).observe(latency_s)
            HTTP_REQUESTS_TOTAL.labels(
                service="okx", method=method, endpoint=endpoint, status=str(http_status)
            ).inc()

            # Проверка на ошибки API
            if data.get("code") != "0":
                error_msg = data.get("msg", "Unknown error")
                logger.error(f"OKX API error: {error_msg}, data: {data}")
                HTTP_REQUESTS_TOTAL.labels(
                    service="okx", method=method, endpoint=endpoint, status=f"api_{data.get('code')}"
                ).inc()
                raise ExchangeError(
                    f"OKX API error: {error_msg}",
                    exchange="okx",
                    operation=endpoint,
                )

            return data.get("data", [])

        except TimeoutError:
            HTTP_REQUESTS_TOTAL.labels(
                service="okx", method=method, endpoint=endpoint, status="timeout"
            ).inc()
            logger.error(f"OKX API timeout: {endpoint}")
            raise
        except aiohttp.ClientError as e:
            HTTP_REQUESTS_TOTAL.labels(
                service="okx", method=method, endpoint=endpoint, status="client_error"
            ).inc()
            logger.error(f"OKX API client error: {e}")
            raise

    # === Инструменты ===

    async def get_instruments(self, symbol: str | None = None) -> list[Instrument]:
        """Получить инструменты"""
        params = {"instType": "SPOT"}
        if symbol:
            params["instId"] = symbol

        try:
            data = await self._request("GET", OKX_ENDPOINTS["spot"]["instruments"], params=params, signed=False)

            instruments = []
            for item in data:
                inst = self._parse_instrument(item)
                self._instrument_cache[inst.symbol] = inst
                instruments.append(inst)

            return instruments

        except Exception as e:
            logger.error(f"Error getting instruments: {e}")
            # Вернуть из кэша если есть
            if symbol:
                return [self._instrument_cache.get(symbol)] if symbol in self._instrument_cache else []
            return list(self._instrument_cache.values())

    async def get_instrument(self, symbol: str) -> Instrument | None:
        """Получить инструмент"""
        # Сначала проверяем кэш
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]

        instruments = await self.get_instruments(symbol)
        return instruments[0] if instruments else None

    def _parse_instrument(self, data: dict[str, Any]) -> Instrument:
        """Разобрать инструмент из ответа OKX"""
        return Instrument(
            exchange="okx",
            symbol=data.get("instId", ""),
            base_asset=data.get("baseCcy", ""),
            quote_asset=data.get("quoteCcy", ""),
            min_quantity=Decimal(data.get("minSz", "0")),
            min_notional=Decimal(data.get("minSz", "0")) * Decimal(data.get("last", "0")),
            step_size=Decimal(data.get("ctVal", "0.001")) if data.get("ctVal") else Decimal("0.001"),
            tick_size=Decimal(data.get("tickSz", "0.01")),
            price_precision=int(data.get("tickSz", "0.01").replace(".", "").count("0")),
            quantity_precision=int(data.get("lotSz", "0.001").replace(".", "").count("0")),
            trading_status=data.get("state", "trading"),
            fee_rate=Decimal("0.001"),  # OKX стандартная комиссия 0.1%
            contract_type="spot",
        )

    # === Рыночные данные ===

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        since: int | None = None,
        limit: int = 1000
    ) -> list[Candle]:
        """Получить свечи"""
        # OKX использует формат "BTC-USDT"; нормализуем "BTC/USDT".
        okx_symbol = symbol.replace("/", "-")
        # Конвертация таймфрейма OKX
        okx_granularity = self._convert_timeframe(timeframe)

        params = {
            "instId": okx_symbol,
            "bar": okx_granularity,
            "limit": min(limit, 1000)  # OKX ограничение 1000
        }

        if since:
            params["before"] = since

        try:
            data = await self._request("GET", OKX_ENDPOINTS["spot"]["candles"], params=params, signed=False)

            candles = []
            for item in data:
                candle = Candle(
                    exchange="okx",
                    symbol=okx_symbol,
                    timeframe=timeframe,
                    open_time=int(item[0]),
                    open=Decimal(item[1]),
                    high=Decimal(item[2]),
                    low=Decimal(item[3]),
                    close=Decimal(item[4]),
                    volume=Decimal(item[5]),
                    quote_volume=Decimal(item[6]) if len(item) > 6 else Decimal("0"),
                    trades_count=0
                )
                candles.append(candle)

            return candles

        except Exception as e:
            logger.error(f"Error getting candles for {symbol}: {e}")
            return []

    async def get_recent_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100
    ) -> list[Candle]:
        """Получить последние свечи"""
        return await self.get_candles(symbol, timeframe, limit=limit)

    async def get_trades(
        self,
        symbol: str,
        since: int | None = None,
        limit: int = 100
    ) -> list[Trade]:
        """Получить торговлю"""
        params = {
            "instId": symbol,
            "limit": limit
        }

        if since:
            params["before"] = since

        try:
            data = await self._request("GET", OKX_ENDPOINTS["spot"]["trades"], params=params, signed=False)

            trades = []
            for item in data:
                trade = Trade(
                    trade_id=item[0],
                    exchange="okx",
                    symbol=symbol,
                    price=Decimal(item[1]),
                    quantity=Decimal(item[2]),
                    side="buy" if item[3] == "buy" else "sell",
                    timestamp=int(item[4]),
                )
                trades.append(trade)

            return trades

        except Exception as e:
            logger.error(f"Error getting trades for {symbol}: {e}")
            return []

    async def get_orderbook(
        self,
        symbol: str,
        depth: int = 20
    ) -> OrderBook:
        """Получить стакан заявок"""
        params = {
            "instId": symbol,
            "sz": depth
        }

        try:
            data = await self._request(
                "GET",
                OKX_ENDPOINTS["spot"]["orderbook"],
                params=params,
                signed=False,
            )

            # ``_request`` возвращает поле ``data`` из ответа OKX, то есть
            # список с одним элементом — словарём стакана.
            book = data[0] if isinstance(data, list) and data else {}

            bids = []
            for bid in (book.get("bids") or [])[:depth]:
                if len(bid) >= 2:
                    bids.append(OrderBookEntry(
                        price=Decimal(bid[0]),
                        quantity=Decimal(bid[1]),
                    ))

            asks = []
            for ask in (book.get("asks") or [])[:depth]:
                if len(ask) >= 2:
                    asks.append(OrderBookEntry(
                        price=Decimal(ask[0]),
                        quantity=Decimal(ask[1]),
                    ))

            # Сортировка: asks по возрастанию, bids по убыванию
            asks.sort(key=lambda x: x.price)
            bids.sort(key=lambda x: x.price, reverse=True)

            return OrderBook(
                symbol=symbol,
                exchange="okx",
                bids=bids,
                asks=asks,
            )

        except Exception as e:
            logger.error(f"Error getting orderbook for {symbol}: {e}")
            return OrderBook(symbol=symbol, exchange="okx", bids=[], asks=[])

    async def get_ticker(self, symbol: str) -> dict[str, Any]:
        """Получить тикер"""
        params = {"instId": symbol}

        try:
            data = await self._request("GET", OKX_ENDPOINTS["spot"]["ticker"], params=params, signed=False)

            if data:
                ticker_data = data[0]
                return {
                    "symbol": symbol,
                    "last": Decimal(ticker_data.get("last", "0")),
                    "bid": Decimal(ticker_data.get("bidPx", "0")),
                    "ask": Decimal(ticker_data.get("askPx", "0")),
                    "high_24h": Decimal(ticker_data.get("high24h", "0")),
                    "low_24h": Decimal(ticker_data.get("low24h", "0")),
                    "volume_24h": Decimal(ticker_data.get("vol24h", "0")),
                    "quote_volume_24h": Decimal(ticker_data.get("volCcy24h", "0")),
                    "price_change_24h": Decimal(ticker_data.get("pretRcl", "0")),
                }

            return {}

        except Exception as e:
            logger.error(f"Error getting ticker for {symbol}: {e}")
            return {}

    def _convert_timeframe(self, timeframe: str) -> str:
        """Конвертировать таймфрейм в формат OKX"""
        mapping = {
            "1m": "1m",
            "3m": "3m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1H": "1H",
            "2H": "2H",
            "4H": "4H",
            "6H": "6H",
            "12H": "12H",
            "1D": "1D",
            "1W": "1W",
            # lowercase aliases used throughout the codebase
            "1h": "1H",
            "2h": "2H",
            "4h": "4H",
            "6h": "6H",
            "12h": "12H",
            "1d": "1D",
            "1w": "1W",
        }
        return mapping.get(timeframe, "1m")

    # === Аккаунт ===

    async def get_account_balance(self) -> dict[str, AccountBalance]:
        """Получить баланс аккаунта"""
        balances = {}

        try:
            if not self.api_key:
                # Режим песочницы / тестовый — возвращаем тестовые балансы
                return {
                    "USDT": AccountBalance(
                        account_id="paper",
                        exchange="okx",
                        asset="USDT",
                        free=Decimal("10000"),
                        locked=Decimal("0"),
                        total=Decimal("10000"),
                    ),
                    "BTC": AccountBalance(
                        account_id="paper",
                        exchange="okx",
                        asset="BTC",
                        free=Decimal("0"),
                        locked=Decimal("0"),
                        total=Decimal("0"),
                    ),
                }

            data = await self._request("GET", OKX_ENDPOINTS["spot"]["account"], signed=True)

            # OKX /api/v5/account/balance возвращает список счетов; детали
            # по активам лежат в details[]. Поля: availBal/cashBal/eq/frozenBal.
            details: list[dict[str, Any]] = []
            for account in data:
                details.extend(account.get("details", []) or [])

            for item in details:
                asset = item.get("ccy", "").strip()
                if not asset:
                    continue
                # eq = общий баланс; availBal = доступный; frozenBal/ordFroz = заблокированный.
                total_raw = item.get("eq") or item.get("cashBal") or "0"
                free_raw = item.get("availBal") or item.get("availableBal") or total_raw
                locked_raw = item.get("frozenBal") or item.get("ordFroz") or "0"
                try:
                    total = Decimal(str(total_raw))
                    free = Decimal(str(free_raw))
                    locked = Decimal(str(locked_raw))
                except Exception:
                    continue

                balances[asset] = AccountBalance(
                    account_id="okx_main",
                    exchange="okx",
                    asset=asset,
                    free=free,
                    locked=locked,
                    total=total,
                )

            return balances

        except Exception as e:
            logger.error(f"Error getting account balance: {e}")
            # В случае ошибки возвращаем пустые балансы
            return {}

    async def get_balances(self, assets: list[str] | None = None) -> dict[str, Decimal]:
        """Получить балансы в виде словаря"""
        balances = await self.get_account_balance()

        if assets:
            return {
                asset: balances.get(asset, AccountBalance("", "okx", asset)).free
                for asset in assets
            }

        return {
            asset: balance.free
            for asset, balance in balances.items()
        }

    # === Ордера ===

    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        price: Decimal | None = None,
        stop_price: Decimal | None = None,
        take_profit_price: Decimal | None = None,
        client_order_id: str | None = None,
        **kwargs
    ) -> Order:
        """Разместить ордер"""
        if not self.enabled:
            raise ExchangeError("Exchange is disabled", exchange="okx", operation="place_order")

        # Валидация
        errors = self.validate_order(symbol, side, order_type, quantity, price)
        if errors:
            raise ValueError(f"Order validation failed: {errors}")

        # Проверка минимальных требований
        min_errors = await self.check_min_order_requirements(symbol, quantity, price)
        if min_errors:
            raise ValueError(f"Minimum requirements not met: {min_errors}")

        try:
            params = {
                "instId": symbol,
                "tdRep": "0",  # spot trading
                "side": side.upper(),
                "ordType": order_type.upper(),
                "sz": str(quantity),
            }

            if price:
                params["px"] = str(price)

            if client_order_id:
                params["clOrdId"] = client_order_id

            body = params
            data = await self._request("POST", OKX_ENDPOINTS["spot"]["place_order"], body=body, signed=True)

            if data:
                order_data = data[0]
                return Order(
                    id=order_data.get("ordId"),
                    client_order_id=order_data.get("clOrdId"),
                    exchange="okx",
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    quantity=quantity,
                    price=price,
                    status=order_data.get("state", "new").lower(),
                    created_at=datetime.utcnow(),
                )

            raise ExchangeError("No response from OKX", exchange="okx", operation="place_order")

        except Exception as e:
            logger.error(f"Error placing order: {e}")
            raise

    async def cancel_order(
        self,
        symbol: str,
        order_id: str
    ) -> bool:
        """Отменить ордер"""
        try:
            params = {
                "instId": symbol,
                "ordId": order_id,
            }

            data = await self._request("POST", OKX_ENDPOINTS["spot"]["cancel_order"], body=params, signed=True)

            if data and data[0].get("code") == "0":
                return True

            logger.warning(f"Failed to cancel order {order_id}: {data}")
            return False

        except Exception as e:
            logger.error(f"Error canceling order: {e}")
            return False

    async def cancel_all_orders(self, symbol: str) -> int:
        """Отменить все ордера по символу"""
        try:
            params = {"instId": symbol}
            data = await self._request("POST", "/api/v5/trade/cancel-all", body=params, signed=True)

            if data:
                return int(data[0].get("suid", "0"))

            return 0

        except Exception as e:
            logger.error(f"Error canceling all orders: {e}")
            return 0

    async def get_order(self, symbol: str, order_id: str) -> Order | None:
        """Получить ордер"""
        try:
            params = {
                "instId": symbol,
                "ordId": order_id,
            }

            data = await self._request("GET", OKX_ENDPOINTS["spot"]["get_order"], params=params, signed=True)

            if data:
                order_data = data[0]
                return Order(
                    id=order_data.get("ordId"),
                    client_order_id=order_data.get("clOrdId"),
                    exchange="okx",
                    symbol=symbol,
                    side=order_data.get("side", "").lower(),
                    order_type=order_data.get("ordType", "").lower(),
                    quantity=Decimal(order_data.get("sz", "0")),
                    price=Decimal(order_data.get("px", "0")) if order_data.get("px") else None,
                    status=order_data.get("state", "new").lower(),
                    filled_quantity=Decimal(order_data.get("accSz", "0")),
                    filled_price=Decimal(order_data.get("avgPx", "0")) if order_data.get("avgPx") else None,
                    filled_fees=Decimal(order_data.get("fee", "0")),
                    created_at=datetime.utcnow(),
                )

            return None

        except Exception as e:
            logger.error(f"Error getting order: {e}")
            return None

    async def get_open_orders(
        self,
        symbol: str | None = None
    ) -> list[Order]:
        """Получить открытые ордера"""
        try:
            params = {"instType": "SPOT"}
            if symbol:
                params["instId"] = symbol

            data = await self._request("GET", OKX_ENDPOINTS["spot"]["open_orders"], params=params, signed=True)

            orders = []
            for item in data:
                orders.append(Order(
                    id=item.get("ordId"),
                    client_order_id=item.get("clOrdId"),
                    exchange="okx",
                    symbol=item.get("instId", symbol or ""),
                    side=item.get("side", "").lower(),
                    order_type=item.get("ordType", "").lower(),
                    quantity=Decimal(item.get("sz", "0")),
                    price=Decimal(item.get("px", "0")) if item.get("px") else None,
                    status=item.get("state", "new").lower(),
                    filled_quantity=Decimal(item.get("accSz", "0")),
                    created_at=datetime.utcnow(),
                ))

            return orders

        except Exception as e:
            logger.error(f"Error getting open orders: {e}")
            return []

    async def get_order_history(
        self,
        symbol: str,
        since: int | None = None,
        limit: int = 100
    ) -> list[Order]:
        """Получить историю ордеров"""
        # В OKX история ордеров — отдельной эндпоинт
        # Для простоты используем get_open_orders с фильтрацией
        return await self.get_open_orders(symbol)

    # === Позиции ===

    async def get_positions(self) -> list[Position]:
        """Получить позиции"""
        # В спот-торговле позиции хранятся в балансах
        # Для простоты возвращаем пустой список
        return []

    async def close_position(
        self,
        symbol: str,
        quantity: Decimal | None = None,
        price: Decimal | None = None
    ) -> bool:
        """Закрыть позицию"""
        # В спот-торговле закрытие позиции = продажа
        try:
            await self.place_order(
                symbol=symbol,
                side="sell",
                order_type="market",
                quantity=quantity or Decimal("0"),
            )
            return True
        except Exception as e:
            logger.error(f"Error closing position: {e}")
            return False

    # === Здоровье ===

    async def get_exchange_health(self) -> ExchangeHealth:
        """Получить здоровье биржи"""
        return ExchangeHealth(
            exchange="okx",
            status=ExchangeHealthStatus.HEALTHY if self._is_connected else ExchangeHealthStatus.OFFLINE,
            api_latency_ms=self._last_latency,
            websocket_status="DISCONNECTED",  # WebSocket handled separately
            rejected_orders_count=0,
            execution_quality_score=1.0,
            price_anomaly_detected=False,
            maintenance_mode=False,
            error_rate=0.0,
        )

    async def test_connection(self) -> bool:
        """Проверить соединение"""
        try:
            # Проверка через получение инструментов
            instruments = await self.get_instruments()
            if instruments:
                self._is_connected = True
                return True
            return False
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            self._is_connected = False
            return False


# Фабрика для OKX
def create_okx_adapter(config: dict[str, Any]) -> OKXClient:
    """Создать OKX адаптер"""
    return OKXClient(config)
