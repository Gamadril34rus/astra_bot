"""
ASTRA BOT — BingX REST API Client (spot).

Заменяет OKX-адаптер в активном контуре (решение: ретир OKX → BingX).

Особенности BingX spot API (https://open-api.bingx.com):
  * Символы в формате ``BTC-USDT`` (дефис).
  * Публичные market-эндпоинты не требуют ключей — бот может работать
    как чистый paper-контур (свечи/стакан/тикеры) без API-ключей.
  * Приватные эндпоинты (баланс, ордера) подписываются HMAC-SHA256:
        signature = HMAC_SHA256(secret, urlencode(keysort(params + timestamp)))
    и передаются в query как ``&signature=...``, ключ — в заголовке
    ``X-BX-APIKEY``. Passphrase у BingX нет.
  * Ответ: ``{"code": 0, "msg": "", "data": ...}`` (code — число).
  * Демо/песочница для spot отсутствует (testnet BingX — только swap),
    поэтому приватный доступ — только к реальному спот-счёту с ключами
    БЕЗ права вывода. Бумажные сделки исполняются PaperBroker'ом, на
    биржу ордера не уходят.
"""

import asyncio
import hashlib
import hmac
import logging
import time
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import aiohttp

from ...utils.retry import retry_async
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

# BingX API endpoints (spot)
BINGX_API_BASE = "https://open-api.bingx.com"
BINGX_SPOT_PREFIX = "/openApi/spot/v1"

BINGX_ENDPOINTS = {
    "spot": {
        "server_time": f"{BINGX_SPOT_PREFIX}/server/time",
        "instruments": f"{BINGX_SPOT_PREFIX}/common/symbols",
        "candles": f"{BINGX_SPOT_PREFIX}/market/kline",
        "ticker_24hr": f"{BINGX_SPOT_PREFIX}/ticker/24hr",
        "orderbook": f"{BINGX_SPOT_PREFIX}/market/depth",
        "trades": f"{BINGX_SPOT_PREFIX}/market/trades",
        "account": f"{BINGX_SPOT_PREFIX}/account/balance",
        "place_order": f"{BINGX_SPOT_PREFIX}/trade/order",
        "cancel_order": f"{BINGX_SPOT_PREFIX}/trade/cancel",
        "get_order": f"{BINGX_SPOT_PREFIX}/trade/query",
        "open_orders": f"{BINGX_SPOT_PREFIX}/trade/openOrders",
        "order_history": f"{BINGX_SPOT_PREFIX}/trade/historyOrders",
    }
}

# Свои интервалы BingX (lowercase). Ключ — любой регистр.
_BINGX_TIMEFRAMES = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "8h": "8h", "12h": "12h",
    "1d": "1d", "3d": "3d", "1w": "1w", "1M": "1M",
}

# Спот-ордера BingX: статусы открытого ордера
_OPEN_STATUSES = {"new", "pending", "partially_filled"}


class BingXClient(ExchangeAdapter):
    """
    BingX Exchange REST API Client (spot).

    Реализует интерфейс ExchangeAdapter + методы, которые вызывают
    TradingEngine / telegram-бот / скрипты. Данные сортируются по времени
    по возрастанию (свежие — в конце списка), как ожидает TradingEngine.
    """

    exchange_name = "bingx"
    exchange_type = "bingx"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get("api_key", "")
        self.api_secret = config.get("api_secret", "")
        # У BingX нет passphrase; оставляем поле для совместимости конфигов.
        self.passphrase = config.get("passphrase")
        self.sandbox = config.get("sandbox", False)
        self.base_url = config.get("base_url", BINGX_API_BASE).rstrip("/")
        self.enabled = config.get("enabled", True)
        self.contract_type = config.get("contract_type", "spot")

        self._session: aiohttp.ClientSession | None = None
        self._is_connected = False
        self._last_latency = 0.0

        # Клиентский rate limiting (token bucket). BingX: market API —
        # 500 req/10s на IP; приватные эндпоинты лимитируются отдельно.
        # По умолчанию консервативные 5 req/s, можно переопределить.
        self._rate_limit_qps = float(config.get("rate_limit_qps", 5))
        self._rate_bucket = self._rate_limit_qps
        self._rate_last = 0.0
        self._rate_lock = asyncio.Lock()

        # Кэш инструментов
        self._instrument_cache: dict[str, Instrument] = {}

    # ------------------------------------------------------------ helpers

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

    def _sign_query(self, params: dict[str, Any]) -> str:
        """Подписать приватный запрос BingX (HMAC-SHA256, hex).

        BingX: signature = HMAC_SHA256(secret, urlencode(keysort(params))),
        где params уже содержат timestamp (unix ms). Возвращает готовую
        строку ``urlencoded_params&signature=...``.
        """
        params = dict(params)
        params["timestamp"] = int(time.time() * 1000)
        ordered = dict(sorted(params.items()))
        payload = urlencode(ordered)
        digest = hmac.new(
            self.api_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{payload}&signature={digest}"

    @retry_async(attempts=3, delays=(2.0, 5.0, 15.0))
    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        signed: bool = False,
    ) -> dict[str, Any]:
        """Отправить запрос к BingX API, вернуть полный JSON-ответ.

        В отличие от OKX-клиента возвращает весь объект (``data`` бывает и
        списком, и словарём); проверку ``code`` делает здесь же.
        """
        if self._session is None:
            raise RuntimeError("BingXClient is not initialized; call initialize() first")

        query = ""
        headers = {}
        if signed:
            if not self.api_key or not self.api_secret:
                raise ExchangeError(
                    "BingX API keys not configured",
                    exchange="bingx",
                    operation=endpoint,
                )
            headers = {"X-BX-APIKEY": self.api_key}
            # Подпись считается по urlencode(keysort(params + timestamp)).
            query = "?" + self._sign_query(params or {})
        else:
            if params:
                query = "?" + urlencode(params)

        url = f"{self.base_url}{endpoint}{query}"

        await self._acquire_rate_token()
        start_time = time.time()
        http_status = 0
        try:
            if method == "GET":
                async with self._session.get(url, headers=headers) as resp:
                    http_status = resp.status
                    data = await resp.json()
            elif method == "POST":
                # BingX spot v1 принимает параметры в query (form-encoded
                # body также допустим; следуем схеме подписи по query).
                async with self._session.post(url, headers=headers) as resp:
                    http_status = resp.status
                    data = await resp.json()
            else:
                raise ValueError(f"Unsupported method: {method}")

            latency_s = time.time() - start_time
            self._last_latency = latency_s * 1000

            HTTP_REQUEST_LATENCY.labels(
                service="bingx", endpoint=endpoint
            ).observe(latency_s)
            HTTP_REQUESTS_TOTAL.labels(
                service="bingx", method=method, endpoint=endpoint,
                status=str(http_status),
            ).inc()

            code = data.get("code")
            if code not in (0, "0"):
                error_msg = data.get("msg") or data.get("error") or "Unknown error"
                logger.error("BingX API error: %s (code=%s), url=%s", error_msg, code, url)
                HTTP_REQUESTS_TOTAL.labels(
                    service="bingx", method=method, endpoint=endpoint,
                    status=f"api_{code}",
                ).inc()
                raise ExchangeError(
                    f"BingX API error: {error_msg}",
                    exchange="bingx",
                    operation=endpoint,
                )
            return data

        except TimeoutError:
            HTTP_REQUESTS_TOTAL.labels(
                service="bingx", method=method, endpoint=endpoint, status="timeout"
            ).inc()
            logger.error("BingX API timeout: %s", endpoint)
            raise
        except aiohttp.ClientError as exc:
            HTTP_REQUESTS_TOTAL.labels(
                service="bingx", method=method, endpoint=endpoint,
                status="client_error",
            ).inc()
            logger.error("BingX API client error: %s", exc)
            raise

    async def initialize(self):
        """Инициализация HTTP-сессии."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        logger.info("BingX client initialized (spot), base_url=%s", self.base_url)

    async def close(self):
        """Закрыть HTTP-сессию."""
        if self._session:
            await self._session.close()
            self._session = None

    def _convert_timeframe(self, timeframe: str) -> str:
        """Конвертировать таймфрейм в формат BingX (lowercase, без 'min')."""
        key = (timeframe or "").lower()
        # Принимаем и "1D"/"4H"/"1min"-подобные варианты.
        key = key.replace("min", "m").replace("day", "d").replace("week", "w")
        interval = _BINGX_TIMEFRAMES.get(key)
        if interval is None:
            # Пытаемся угадать: "60m" → "1h", "1440m" → "1d".
            if key.endswith("m") and key[:-1].isdigit():
                minutes = int(key[:-1])
                for alias, iv in (("1h", 60), ("1d", 1440), ("1w", 10080)):
                    if minutes == iv:
                        return alias
            raise ValueError(f"Unsupported timeframe for BingX: {timeframe}")
        return interval

    def _precision_from_str(self, raw: Any, default: int = 4) -> int:
        """Число знаков после запятой из строки/числа вроде '0.00001'."""
        if raw is None:
            return default
        s = str(raw)
        if "." in s:
            return len(s.split(".", 1)[1].rstrip("0")) or 0
        return default

    # === Инструменты ===

    def _parse_instrument(self, item: dict[str, Any]) -> Instrument:
        """Разобрать инструмент из ответа /common/symbols."""
        symbol = item.get("symbol", "")
        base_asset = item.get("currency") or symbol.split("-")[0]
        quote_asset = item.get("tradeCurrency") or (symbol.split("-")[1] if "-" in symbol else "")
        status = item.get("status")
        api_buy = item.get("apiStateBuy")
        api_sell = item.get("apiStateSell")
        # status=1 + разрешены и покупки, и продажи → торгуется.
        active = str(status) == "1" and bool(api_buy) and bool(api_sell)
        tick = item.get("tickSize") or "0.00000001"
        step = item.get("stepSize") or "0.00000001"
        try:
            tick_size = Decimal(str(tick))
            step_size = Decimal(str(step))
        except Exception:
            tick_size = Decimal("0.00000001")
            step_size = Decimal("0.00000001")
        try:
            min_quantity = Decimal(str(item.get("minQty") or 0))
        except Exception:
            min_quantity = Decimal("0")
        try:
            min_notional = Decimal(str(item.get("minNotional") or item.get("minTradeValue") or 0))
        except Exception:
            min_notional = Decimal("0")
        return Instrument(
            exchange="bingx",
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            min_quantity=min_quantity,
            min_notional=min_notional,
            step_size=step_size,
            tick_size=tick_size,
            price_precision=self._precision_from_str(item.get("pricePrecision"), 8),
            quantity_precision=self._precision_from_str(item.get("quantityPrecision"), 8),
            trading_status="trading" if active else "halt",
            fee_rate=Decimal("0.001"),  # BingX spot: 0.1% taker/maker base
            contract_type="spot",
        )

    async def get_instruments(self, symbol: str | None = None) -> list[Instrument]:
        """Получить метаданные инструментов (публичный эндпоинт)."""
        try:
            resp = await self._request("GET", BINGX_ENDPOINTS["spot"]["instruments"])
            data = resp.get("data") or {}
            rows = data.get("symbols", []) if isinstance(data, dict) else data
            instruments: list[Instrument] = []
            for item in rows:
                inst = self._parse_instrument(item)
                if symbol and inst.symbol != symbol.replace("/", "-"):
                    continue
                self._instrument_cache[inst.symbol] = inst
                instruments.append(inst)
            return instruments
        except Exception as exc:
            logger.error("Error getting BingX instruments: %s", exc)
            if symbol:
                cached = self._instrument_cache.get(symbol.replace("/", "-"))
                return [cached] if cached else []
            return list(self._instrument_cache.values())

    async def get_instrument(self, symbol: str) -> Instrument | None:
        """Получить один инструмент."""
        normalized = symbol.replace("/", "-")
        if normalized in self._instrument_cache:
            return self._instrument_cache[normalized]
        instruments = await self.get_instruments(normalized)
        return instruments[0] if instruments else None

    # === Рыночные данные ===

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        since: int | None = None,
        limit: int = 1000,
    ) -> list[Candle]:
        """Получить свечи (по возрастанию времени: свежие — в конце).

        BingX spot kline привязана к UTC+8; параметр ``timeZone=0``
        выравнивает границы свечей по UTC (проверено в ccxt).
        """
        bingx_symbol = symbol.replace("/", "-")
        params: dict[str, Any] = {
            "symbol": bingx_symbol,
            "interval": self._convert_timeframe(timeframe),
            "limit": min(int(limit), 1000),
            "timeZone": 0,
        }
        if since:
            params["startTime"] = int(since)
        try:
            resp = await self._request("GET", BINGX_ENDPOINTS["spot"]["candles"], params=params)
            data = resp.get("data") or []
            if isinstance(data, dict):
                data = data.get("klines", data.get("data", [])) or []
            candles: list[Candle] = []
            for item in data:
                candle = self._parse_candle(item, bingx_symbol, timeframe)
                if candle is not None:
                    candles.append(candle)
            candles.sort(key=lambda c: c.open_time)
            return candles
        except Exception as exc:
            logger.error("Error getting BingX candles for %s: %s", symbol, exc)
            return []

    def _parse_candle(
        self, item: Any, symbol: str, timeframe: str
    ) -> Candle | None:
        """Разобрать свечу: BingX возвращает и массив, и объект."""
        try:
            if isinstance(item, (list, tuple)):
                if len(item) < 6:
                    return None
                open_time = int(item[0])
                values = [Decimal(str(item[i])) for i in range(1, 6)]
                quote_volume = Decimal(str(item[7])) if len(item) > 7 else Decimal("0")
                return Candle(
                    exchange="bingx",
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time=open_time,
                    open=values[0], high=values[1], low=values[2],
                    close=values[3], volume=values[4],
                    quote_volume=quote_volume,
                    trades_count=0,
                )
            if isinstance(item, dict):
                open_time = int(item.get("time") or item.get("openTime") or 0)
                if not open_time:
                    return None
                return Candle(
                    exchange="bingx",
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time=open_time,
                    open=Decimal(str(item.get("open", "0"))),
                    high=Decimal(str(item.get("high", "0"))),
                    low=Decimal(str(item.get("low", "0"))),
                    close=Decimal(str(item.get("close", "0"))),
                    volume=Decimal(str(item.get("volume", "0"))),
                    quote_volume=Decimal(str(item.get("quoteVolume", "0"))),
                    trades_count=int(item.get("n", 0) or 0),
                )
        except Exception as exc:
            logger.debug("BingX candle parse skipped: %s", exc)
            return None
        return None

    async def get_recent_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
    ) -> list[Candle]:
        """Получить последние свечи."""
        return await self.get_candles(symbol, timeframe, limit=limit)

    async def fetch_kline_page(
        self,
        symbol: str,
        timeframe: str,
        end_time_ms: int | None = None,
        limit: int = 1000,
    ) -> list[Candle]:
        """Одна «страница» истории свечей ДО ``end_time_ms`` (без глотания
        ошибок — для загрузчиков истории, которые делают свои retry).

        Используется историческими загрузчиками (обучение/self-play).
        """
        bingx_symbol = symbol.replace("/", "-")
        params: dict[str, Any] = {
            "symbol": bingx_symbol,
            "interval": self._convert_timeframe(timeframe),
            "limit": min(int(limit), 1000),
            "timeZone": 0,
        }
        if end_time_ms:
            params["endTime"] = int(end_time_ms)
        resp = await self._request("GET", BINGX_ENDPOINTS["spot"]["candles"], params=params)
        data = resp.get("data") or []
        if isinstance(data, dict):
            data = data.get("klines", data.get("data", [])) or []
        candles: list[Candle] = []
        for item in data:
            candle = self._parse_candle(item, bingx_symbol, timeframe)
            if candle is not None:
                candles.append(candle)
        candles.sort(key=lambda c: c.open_time)
        return candles

    async def get_candles_before(
        self,
        symbol: str,
        timeframe: str,
        end_time_ms: int | None = None,
        limit: int = 1000,
    ) -> list[Candle]:
        """Свечи, закрытые до ``end_time_ms`` (обёртка с мягкой ошибкой)."""
        try:
            return await self.fetch_kline_page(
                symbol, timeframe, end_time_ms=end_time_ms, limit=limit
            )
        except Exception as exc:
            logger.error("Error getting BingX candles before %s: %s", end_time_ms, exc)
            return []

    async def get_trades(
        self,
        symbol: str,
        since: int | None = None,
        limit: int = 100,
    ) -> list[Trade]:
        """Получить последние сделки."""
        params: dict[str, Any] = {
            "symbol": symbol.replace("/", "-"),
            "limit": min(int(limit), 100),
        }
        if since:
            params["startTime"] = int(since)
        try:
            resp = await self._request("GET", BINGX_ENDPOINTS["spot"]["trades"], params=params)
            data = resp.get("data") or []
            trades: list[Trade] = []
            for item in data:
                try:
                    is_buyer_maker = bool(item.get("buyerMaker"))
                    trades.append(Trade(
                        trade_id=str(item.get("id", "")),
                        exchange="bingx",
                        symbol=symbol,
                        price=Decimal(str(item.get("price", "0"))),
                        quantity=Decimal(str(item.get("qty", "0"))),
                        side="sell" if is_buyer_maker else "buy",
                        timestamp=int(item.get("time", 0)),
                    ))
                except Exception:
                    continue
            return trades
        except Exception as exc:
            logger.error("Error getting BingX trades for %s: %s", symbol, exc)
            return []

    async def get_orderbook(
        self,
        symbol: str,
        depth: int = 20,
    ) -> OrderBook:
        """Получить стакан заявок (v1: до 20 уровней)."""
        params: dict[str, Any] = {
            "symbol": symbol.replace("/", "-"),
            "limit": min(max(int(depth), 1), 20),
        }
        try:
            resp = await self._request("GET", BINGX_ENDPOINTS["spot"]["orderbook"], params=params)
            data = resp.get("data") or {}
            if isinstance(data, list):
                data = data[0] if data else {}
            bids_raw = data.get("bids") or []
            asks_raw = data.get("asks") or []

            def _entries(raw: list) -> list[OrderBookEntry]:
                out: list[OrderBookEntry] = []
                for row in raw[:depth]:
                    try:
                        if len(row) >= 2:
                            out.append(OrderBookEntry(
                                price=Decimal(str(row[0])),
                                quantity=Decimal(str(row[1])),
                            ))
                    except Exception:
                        continue
                return out

            bids = _entries(bids_raw)
            asks = _entries(asks_raw)
            asks.sort(key=lambda x: x.price)
            bids.sort(key=lambda x: x.price, reverse=True)
            return OrderBook(
                symbol=symbol,
                exchange="bingx",
                bids=bids,
                asks=asks,
            )
        except Exception as exc:
            logger.error("Error getting BingX orderbook for %s: %s", symbol, exc)
            return OrderBook(symbol=symbol, exchange="bingx", bids=[], asks=[])

    async def get_ticker(self, symbol: str) -> dict[str, Any]:
        """Получить 24h-тикер. Ключи совместимы с OKX-клиентом
        (``last``, ``bid``, ``ask``, ``high_24h``...), значения — Decimal."""
        params: dict[str, Any] = {"symbol": symbol.replace("/", "-")}
        try:
            resp = await self._request("GET", BINGX_ENDPOINTS["spot"]["ticker_24hr"], params=params)
            data = resp.get("data") or {}
            if isinstance(data, list):
                data = data[0] if data else {}
            if not data:
                return {}
            return {
                "symbol": symbol,
                "last": Decimal(str(data.get("lastPrice", "0"))),
                "bid": Decimal(str(data.get("bidPrice", "0"))),
                "ask": Decimal(str(data.get("askPrice", "0"))),
                "high_24h": Decimal(str(data.get("highPrice", "0"))),
                "low_24h": Decimal(str(data.get("lowPrice", "0"))),
                "volume_24h": Decimal(str(data.get("volume", "0"))),
                "quote_volume_24h": Decimal(str(data.get("quoteVolume", "0"))),
                "open_24h": Decimal(str(data.get("openPrice", "0"))),
                "price_change_24h": Decimal(str(data.get("priceChange", "0"))),
            }
        except Exception as exc:
            logger.error("Error getting BingX ticker for %s: %s", symbol, exc)
            return {}

    # === Аккаунт ===

    async def get_account_balance(self) -> dict[str, AccountBalance]:
        """Получить баланс спот-аккаунта (приватный эндпоинт).

        Без ключей возвращает пустой словарь (никаких «бумажных» балансов).
        """
        balances: dict[str, AccountBalance] = {}
        if not self.api_key or not self.api_secret:
            return balances
        try:
            resp = await self._request(
                "GET", BINGX_ENDPOINTS["spot"]["account"], signed=True
            )
            data = resp.get("data") or {}
            rows = data.get("balances", []) if isinstance(data, dict) else (data or [])
            for item in rows:
                asset = str(item.get("asset") or "").strip()
                if not asset:
                    continue
                try:
                    free = Decimal(str(item.get("free") or "0"))
                    locked = Decimal(str(item.get("locked") or "0"))
                except Exception:
                    continue
                balances[asset] = AccountBalance(
                    account_id="bingx_spot",
                    exchange="bingx",
                    asset=asset,
                    free=free,
                    locked=locked,
                    total=free + locked,
                )
            return balances
        except Exception as exc:
            logger.warning("Error getting BingX account balance: %s", exc)
            return balances

    async def get_funding_balance(self) -> dict[str, AccountBalance]:
        """Отдельного funding-аккаунта у BingX spot нет.

        Метод оставлен для совместимости интерфейса; возвращает {}.
        """
        return {}

    async def get_balances(self, assets: list[str] | None = None) -> dict[str, Decimal]:
        """Получить балансы в виде словаря asset → free."""
        balances = await self.get_account_balance()
        if assets:
            return {
                asset: balances.get(asset, AccountBalance(exchange="bingx", asset=asset)).free
                for asset in assets
            }
        return {asset: balance.free for asset, balance in balances.items()}

    # === Ордера (прямой live-режим; paper-контур их не использует) ===

    @staticmethod
    def _order_status(status: str) -> str:
        """Нормализовать статус ордера BingX к нижнему регистру."""
        s = (status or "").lower()
        aliases = {"pending": "new", "pending_cancel": "canceled"}
        return aliases.get(s, s)

    def _parse_order(self, item: dict[str, Any], symbol: str) -> Order | None:
        try:
            qty = Decimal(str(item.get("origQty") or item.get("quantity") or "0"))
            filled = Decimal(str(item.get("executedQty") or "0"))
            price_raw = item.get("price")
            status = self._order_status(str(item.get("status") or "new"))
            return Order(
                id=str(item.get("orderId") or ""),
                client_order_id=item.get("clientOrderID"),
                exchange="bingx",
                symbol=item.get("symbol") or symbol,
                side=str(item.get("side") or "").lower(),
                order_type=str(item.get("type") or "limit").lower(),
                quantity=qty,
                price=Decimal(str(price_raw)) if price_raw not in (None, "", 0) else None,
                status=status,
                filled_quantity=filled,
                filled_price=Decimal(str(item.get("avgPrice") or "0")) or None,
                filled_fees=Decimal(str(item.get("fee") or "0")),
                created_at=datetime.utcnow(),
            )
        except Exception as exc:
            logger.debug("BingX order parse skipped: %s", exc)
            return None

    @staticmethod
    def _orders_from_payload(data: Any) -> list[dict]:
        """Достать список ордеров из ``data`` (разные формы ответов)."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("orders", "order"):
                val = data.get(key)
                if isinstance(val, list):
                    return val
                if isinstance(val, dict):
                    return [val]
        return []

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
        """Разместить спот-ордер (BUY/SELL, LIMIT/MARKET)."""
        if not self.enabled:
            raise ExchangeError("Exchange is disabled", exchange="bingx", operation="place_order")
        errors = self.validate_order(symbol, side, order_type, quantity, price)
        if errors:
            raise ValueError(f"Order validation failed: {errors}")

        if stop_price is not None or take_profit_price is not None:
            raise NotImplementedError(
                "BingX spot REST v1 не поддерживает SL/TP в одном ордере"
            )

        params: dict[str, Any] = {
            "symbol": symbol.replace("/", "-"),
            "side": "BUY" if str(side).lower() in ("buy", "long") else "SELL",
            "type": str(order_type).upper(),
            "quantity": str(quantity),
        }
        if price is not None and str(order_type).lower() == "limit":
            params["price"] = str(price)
            params["timeInForce"] = "GTC"
        if client_order_id:
            params["newClientOrderID"] = client_order_id

        resp = await self._request("POST", BINGX_ENDPOINTS["spot"]["place_order"],
                                   params=params, signed=True)
        data = resp.get("data") or {}
        if isinstance(data, list):
            data = data[0] if data else {}
        order = self._parse_order(data, symbol)
        if order is None:
            raise ExchangeError("No order data in BingX response",
                                exchange="bingx", operation="place_order")
        return order

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Отменить ордер."""
        try:
            params = {"symbol": symbol.replace("/", "-"), "orderId": str(order_id)}
            resp = await self._request("POST", BINGX_ENDPOINTS["spot"]["cancel_order"],
                                       params=params, signed=True)
            data = resp.get("data") or {}
            if isinstance(data, dict) and data.get("success") is False:
                return False
            # Успех = code 0 (проверен в _request).
            return True
        except Exception as exc:
            logger.error("Error canceling BingX order %s: %s", order_id, exc)
            return False

    async def cancel_all_orders(self, symbol: str) -> int:
        """Отменить все открытые ордера по символу; вернуть число отмен."""
        try:
            open_orders = await self.get_open_orders(symbol)
            cancelled = 0
            for order in open_orders:
                if order.id and await self.cancel_order(symbol, order.id):
                    cancelled += 1
            return cancelled
        except Exception as exc:
            logger.error("Error canceling all BingX orders for %s: %s", symbol, exc)
            return 0

    async def get_order(self, symbol: str, order_id: str) -> Order | None:
        """Получить ордер по ID."""
        try:
            params = {
                "symbol": symbol.replace("/", "-"),
                "orderId": str(order_id),
            }
            resp = await self._request("GET", BINGX_ENDPOINTS["spot"]["get_order"],
                                       params=params, signed=True)
            data = resp.get("data") or {}
            rows = self._orders_from_payload(data)
            if not rows:
                return None
            return self._parse_order(rows[0], symbol)
        except Exception as exc:
            logger.error("Error getting BingX order %s: %s", order_id, exc)
            return None

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        """Получить открытые ордера."""
        try:
            params: dict[str, Any] = {}
            if symbol:
                params["symbol"] = symbol.replace("/", "-")
            resp = await self._request("GET", BINGX_ENDPOINTS["spot"]["open_orders"],
                                       params=params, signed=True)
            data = resp.get("data") or {}
            rows = self._orders_from_payload(data)
            orders = [o for o in (self._parse_order(r, symbol or "") for r in rows) if o]
            # BingX отдаёт только открытые; на всякий случай фильтруем.
            return [o for o in orders if o.status in {"new", "pending", "partially_filled"}]
        except Exception as exc:
            logger.error("Error getting BingX open orders: %s", exc)
            return []

    async def get_order_history(
        self,
        symbol: str,
        since: int | None = None,
        limit: int = 100,
    ) -> list[Order]:
        """Получить историю ордеров."""
        try:
            params: dict[str, Any] = {
                "symbol": symbol.replace("/", "-"),
                "limit": min(int(limit), 100),
            }
            if since:
                params["startTime"] = int(since)
            resp = await self._request("GET", BINGX_ENDPOINTS["spot"]["order_history"],
                                       params=params, signed=True)
            data = resp.get("data") or {}
            rows = self._orders_from_payload(data)
            return [o for o in (self._parse_order(r, symbol) for r in rows) if o]
        except Exception as exc:
            logger.error("Error getting BingX order history for %s: %s", symbol, exc)
            return []

    # === Позиции ===

    async def get_positions(self) -> list[Position]:
        """В спот-торговле позиции хранятся в балансах — возвращаем []."""
        return []

    async def close_position(
        self,
        symbol: str,
        quantity: Decimal | None = None,
        price: Decimal | None = None,
    ) -> bool:
        """Закрыть позицию = продать по рынку."""
        try:
            await self.place_order(
                symbol=symbol,
                side="sell",
                order_type="market",
                quantity=quantity or Decimal("0"),
            )
            return True
        except Exception as exc:
            logger.error("Error closing BingX position: %s", exc)
            return False

    # === Здоровье ===

    async def get_exchange_health(self) -> ExchangeHealth:
        """Текущее «здоровье» соединения."""
        return ExchangeHealth(
            exchange="bingx",
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
        """Проверить соединение (публичные инструменты)."""
        try:
            instruments = await self.get_instruments()
            if instruments:
                self._is_connected = True
                return True
            return False
        except Exception as exc:
            logger.error("BingX connection test failed: %s", exc)
            self._is_connected = False
            return False


# Фабрика для BingX
def create_bingx_adapter(config: dict[str, Any]) -> BingXClient:
    """Создать BingX адаптер."""
    return BingXClient(config)
