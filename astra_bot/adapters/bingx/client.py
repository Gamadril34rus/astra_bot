"""
ASTRA BOT — BingX REST API Client (USDT-M perpetual futures).

Активная биржа контура (решение: ретир OKX → BingX; бот торгует ТОЛЬКО
бессрочными фьючерсами). Рыночные данные — публичные swap-эндпоинты
(https://open-api BingX open-api.bingx.com):

  * Символы в формате ``BTC-USDT`` (дефис), как в споте.
  * Свечи — ``/openApi/swap/v3/quote/klines``; котировки/стакан/сделки/
    mark price/фандинг/OI — ``/openApi/swap/v2/quote/*``.
  * Публичные market-эндпоинты не требуют ключей — paper-контур
    работает без API-ключей.
  * Приватные эндпоинты подписываются HMAC-SHA256:
        signature = HMAC_SHA256(secret, urlencode(keysort(params + timestamp)))
    и передаются в query как ``&signature=...``, ключ — в заголовке
    ``X-BX-APIKEY``. Passphrase у BingX нет.
  * Ответ: ``{"code": 0, "msg": "", "data": ...}`` (code — число).
  * Live-ордера НЕ реализованы сознательно: paper-контур исполняет
    сделки через PaperBroker, на биржу ордера не уходят. Для будущих
    live-тестов у BingX есть VST-демо именно под swap.

Комиссии эмуляции — биржевые (Perpetual Futures Fee Schedule):
тейкер 0.05%, мейкер 0.02%; фандинг — 3 раза в сутки.
"""

import asyncio
import hashlib
import hmac
import logging
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import aiohttp

from ...core.exceptions import ExchangeError
from ...core.metrics import HTTP_REQUEST_LATENCY, HTTP_REQUESTS_TOTAL
from ...utils.retry import retry_async
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

# BingX API endpoints (USDT-M perpetual futures / swap)
BINGX_API_BASE = "https://open-api.bingx.com"
BINGX_SWAP_V2_PREFIX = "/openApi/swap/v2"
BINGX_SWAP_V3_PREFIX = "/openApi/swap/v3"

BINGX_ENDPOINTS = {
    "swap": {
        "server_time": f"{BINGX_SWAP_V2_PREFIX}/quote/server/time",
        "instruments": f"{BINGX_SWAP_V2_PREFIX}/quote/contracts",
        "candles": f"{BINGX_SWAP_V3_PREFIX}/quote/klines",
        "ticker_24hr": f"{BINGX_SWAP_V2_PREFIX}/quote/ticker",
        "orderbook": f"{BINGX_SWAP_V2_PREFIX}/quote/depth",
        "trades": f"{BINGX_SWAP_V2_PREFIX}/quote/trades",
        "price": f"{BINGX_SWAP_V2_PREFIX}/quote/price",
        "book_ticker": f"{BINGX_SWAP_V2_PREFIX}/quote/bookTicker",
        # Mark price + текущий фандинг одним запросом.
        "premium_index": f"{BINGX_SWAP_V2_PREFIX}/quote/premiumIndex",
        # История ставок фандинга.
        "funding_rate": f"{BINGX_SWAP_V2_PREFIX}/quote/fundingRate",
        # Открытый интерес.
        "open_interest": f"{BINGX_SWAP_V2_PREFIX}/quote/openInterest",
        # Баланс фьючерсного счёта (приватный, только чтение/инфо).
        "account": f"{BINGX_SWAP_V2_PREFIX}/user/balance",
    }
}

# Интервалы свечей BingX swap. Ключ — любой регистр.
_BINGX_TIMEFRAMES = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "12h": "12h",
    "1d": "1d", "1w": "1w", "1M": "1M",
}

# Лимит глубины стакана swap (биржа принимает 5/10/20/50/100).
_MAX_DEPTH = 100
# Безопасный лимит страницы свечей v3.
_MAX_KLINES = 500

_LIVE_DISABLED_MSG = (
    "Live-торговля отключена: paper-контур исполняет сделки через "
    "PaperBroker (BingX USDT-M perps), ордера на биржу не уходят."
)


class BingXClient(ExchangeAdapter):
    """
    BingX Exchange REST API Client (USDT-M perpetual futures).

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
        # Активный контур — только линейные перпетуалы (USDT-M).
        self.contract_type = config.get("contract_type", "linear")

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

        Возвращает весь объект (``data`` бывает и списком, и словарём);
        проверку ``code`` делает здесь же.
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
                # BingX принимает параметры в query (form-encoded
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
        logger.info("BingX client initialized (USDT-M perps), base_url=%s", self.base_url)

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

    @staticmethod
    def _first_dict(data: Any) -> dict[str, Any]:
        """Достать первый словарь из ответа (data бывает dict или [dict])."""
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return data[0] if data and isinstance(data[0], dict) else {}
        return {}

    # === Инструменты ===

    def _parse_instrument(self, item: dict[str, Any]) -> Instrument:
        """Разобрать контракт из ответа /quote/contracts (USDT-M perps)."""
        symbol = item.get("symbol", "")
        parts = symbol.split("-")
        base_asset = (
            item.get("commodityCurrency")
            or item.get("currency")
            or (parts[0] if parts else "")
        )
        quote_asset = (
            item.get("tradeCurrency")
            or (parts[1] if len(parts) > 1 else "")
        )
        status = item.get("status")
        # status=1 → активен; api-флаги, если есть, тоже должны разрешать.
        api_buy = item.get("apiStateBuy", True)
        api_sell = item.get("apiStateSell", True)
        active = str(status) == "1" and bool(api_buy) and bool(api_sell)
        tick = item.get("tickSize") or item.get("minTickSize") or "0.00000001"
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
            min_notional = Decimal(
                str(item.get("minNotional") or item.get("minTradeValue") or 0)
            )
        except Exception:
            min_notional = Decimal("0")
        max_leverage = item.get("maxLeverage")
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
            # BingX USDT-M perps: taker 0.05% (paper всегда тейкер).
            fee_rate=Decimal("0.0005"),
            contract_type="linear",
            metadata={"max_leverage": max_leverage} if max_leverage else {},
        )

    async def get_instruments(self, symbol: str | None = None) -> list[Instrument]:
        """Получить метаданные контрактов (публичный эндпоинт)."""
        try:
            resp = await self._request("GET", BINGX_ENDPOINTS["swap"]["instruments"])
            data = resp.get("data") or []
            if isinstance(data, dict):
                for key in ("contracts", "symbols", "list", "data"):
                    val = data.get(key)
                    if isinstance(val, list):
                        data = val
                        break
                else:
                    data = []
            rows = data if isinstance(data, list) else []
            instruments: list[Instrument] = []
            for item in rows:
                if not isinstance(item, dict):
                    continue
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
        """Получить свечи перпетуал-контракта (по возрастанию времени)."""
        bingx_symbol = symbol.replace("/", "-")
        params: dict[str, Any] = {
            "symbol": bingx_symbol,
            "interval": self._convert_timeframe(timeframe),
            "limit": min(int(limit), _MAX_KLINES),
        }
        if since:
            params["startTime"] = int(since)
        try:
            resp = await self._request("GET", BINGX_ENDPOINTS["swap"]["candles"], params=params)
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
            "limit": min(int(limit), _MAX_KLINES),
        }
        if end_time_ms:
            params["endTime"] = int(end_time_ms)
        resp = await self._request("GET", BINGX_ENDPOINTS["swap"]["candles"], params=params)
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
            resp = await self._request("GET", BINGX_ENDPOINTS["swap"]["trades"], params=params)
            data = resp.get("data") or []
            trades: list[Trade] = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                try:
                    side = str(item.get("side") or "").lower()
                    if not side:
                        # buyerMaker=true → сделка инициирована продавцом.
                        maker = item.get("buyerMaker")
                        if maker is None:
                            maker = item.get("isBuyerMaker")
                        if maker is None:
                            maker = str(item.get("makerSide") or "").upper() == "SELL"
                        side = "sell" if maker else "buy"
                    qty = item.get("qty", item.get("quantity", item.get("volume", "0")))
                    trades.append(Trade(
                        trade_id=str(item.get("id", "")),
                        exchange="bingx",
                        symbol=symbol,
                        price=Decimal(str(item.get("price", "0"))),
                        quantity=Decimal(str(qty)),
                        side=side,
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
        """Получить стакан заявок (swap: до 100 уровней)."""
        params: dict[str, Any] = {
            "symbol": symbol.replace("/", "-"),
            "limit": min(max(int(depth), 1), _MAX_DEPTH),
        }
        try:
            resp = await self._request("GET", BINGX_ENDPOINTS["swap"]["orderbook"], params=params)
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
        """Получить 24h-тикер. Ключи совместимы с прежним клиентом
        (``last``, ``bid``, ``ask``, ``high_24h``...), значения — Decimal."""
        params: dict[str, Any] = {"symbol": symbol.replace("/", "-")}
        try:
            resp = await self._request("GET", BINGX_ENDPOINTS["swap"]["ticker_24hr"], params=params)
            data = self._first_dict(resp.get("data") or {})
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

    # === Фьючерсные данные: mark price, фандинг, открытый интерес ===

    async def get_premium_index(self, symbol: str) -> dict[str, Any]:
        """Mark price + текущий фандинг одним запросом.

        Возвращает ``{"mark_price", "index_price", "funding_rate",
        "next_funding_time_ms"}`` (Decimal/int) или {} при недоступности.
        """
        params: dict[str, Any] = {"symbol": symbol.replace("/", "-")}
        try:
            resp = await self._request(
                "GET", BINGX_ENDPOINTS["swap"]["premium_index"], params=params
            )
            data = self._first_dict(resp.get("data") or {})
            if not data:
                return {}
            rate_raw = data.get("lastFundingRate", data.get("fundingRate", "0"))
            return {
                "mark_price": Decimal(str(data.get("markPrice", "0"))),
                "index_price": Decimal(str(data.get("indexPrice", "0"))),
                "funding_rate": Decimal(str(rate_raw or "0")),
                "next_funding_time_ms": int(data.get("nextFundingTime") or 0),
            }
        except Exception as exc:
            logger.error("Error getting BingX premium index for %s: %s", symbol, exc)
            return {}

    async def get_mark_price(self, symbol: str) -> Decimal | None:
        """Текущая mark price контракта (для ликвидаций). None — недоступна."""
        info = await self.get_premium_index(symbol)
        mark = info.get("mark_price")
        if mark is None or mark <= 0:
            return None
        return mark

    async def get_funding_rate(self, symbol: str) -> dict[str, Any]:
        """Текущая ставка фандинга: ``{"rate", "next_funding_time_ms"}``."""
        info = await self.get_premium_index(symbol)
        if not info:
            return {}
        return {
            "rate": info["funding_rate"],
            "next_funding_time_ms": info["next_funding_time_ms"],
        }

    async def get_mark_and_funding(
        self, symbol: str
    ) -> tuple[Decimal | None, Decimal | None]:
        """(mark price, funding rate) одним запросом — для тика движка."""
        info = await self.get_premium_index(symbol)
        if not info:
            return None, None
        mark = info.get("mark_price")
        return (mark if mark and mark > 0 else None, info.get("funding_rate"))

    async def get_funding_rate_history(
        self,
        symbol: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """История ставок фандинга: ``[{"rate", "funding_time_ms"}]``."""
        params: dict[str, Any] = {
            "symbol": symbol.replace("/", "-"),
            "limit": min(int(limit), 1000),
        }
        try:
            resp = await self._request(
                "GET", BINGX_ENDPOINTS["swap"]["funding_rate"], params=params
            )
            data = resp.get("data") or []
            if isinstance(data, dict):
                data = data.get("fundingRates", data.get("data", [])) or []
            out: list[dict[str, Any]] = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                try:
                    out.append({
                        "rate": Decimal(str(item.get("fundingRate", "0"))),
                        "funding_time_ms": int(
                            item.get("fundingTime", item.get("time", 0)) or 0
                        ),
                    })
                except Exception:
                    continue
            out.sort(key=lambda x: x["funding_time_ms"])
            return out
        except Exception as exc:
            logger.error("Error getting BingX funding history for %s: %s", symbol, exc)
            return []

    async def get_open_interest(self, symbol: str) -> Decimal | None:
        """Открытый интерес контракта (в базовом активе). None — недоступен."""
        params: dict[str, Any] = {"symbol": symbol.replace("/", "-")}
        try:
            resp = await self._request(
                "GET", BINGX_ENDPOINTS["swap"]["open_interest"], params=params
            )
            data = self._first_dict(resp.get("data") or {})
            if not data:
                return None
            try:
                return Decimal(str(data.get("openInterest", "0")))
            except Exception:
                return None
        except Exception as exc:
            logger.error("Error getting BingX open interest for %s: %s", symbol, exc)
            return None

    # === Аккаунт ===

    async def get_account_balance(self) -> dict[str, AccountBalance]:
        """Получить баланс фьючерсного (USDT-M) счёта (приватный эндпоинт).

        Используется только для информации (оценка капитала, /баланс).
        Без ключей возвращает пустой словарь (никаких «бумажных» балансов).
        """
        balances: dict[str, AccountBalance] = {}
        if not self.api_key or not self.api_secret:
            return balances
        try:
            resp = await self._request(
                "GET", BINGX_ENDPOINTS["swap"]["account"], signed=True
            )
            data = resp.get("data") or {}
            rows: Any = data.get("balance", []) if isinstance(data, dict) else data
            if isinstance(rows, dict):
                rows = [rows]
            for item in rows or []:
                if not isinstance(item, dict):
                    continue
                asset = str(item.get("asset") or "").strip()
                if not asset:
                    continue
                try:
                    equity = Decimal(str(item.get("equity", item.get("balance", "0"))))
                    available = Decimal(str(
                        item.get("availableMargin", item.get("balance", "0"))
                    ))
                    used = Decimal(str(item.get("usedMargin", "0")))
                    frozen = Decimal(str(item.get("freezedMargin", "0")))
                except Exception:
                    continue
                balances[asset] = AccountBalance(
                    account_id="bingx_swap",
                    exchange="bingx",
                    asset=asset,
                    free=available,
                    locked=used + frozen,
                    total=equity,
                )
            return balances
        except Exception as exc:
            logger.warning("Error getting BingX account balance: %s", exc)
            return balances

    async def get_funding_balance(self) -> dict[str, AccountBalance]:
        """Отдельного funding-аккаунта нет.

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

    # === Ордера/позиции: live отключён (paper-контур через PaperBroker) ===

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
        """Разместить ордер — НЕ РЕАЛИЗОВАНО (live отключён)."""
        raise NotImplementedError(_LIVE_DISABLED_MSG)

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Отменить ордер — НЕ РЕАЛИЗОВАНО (live отключён)."""
        raise NotImplementedError(_LIVE_DISABLED_MSG)

    async def cancel_all_orders(self, symbol: str) -> int:
        """Отменить все открытые ордера — НЕ РЕАЛИЗОВАНО (live отключён)."""
        raise NotImplementedError(_LIVE_DISABLED_MSG)

    async def get_order(self, symbol: str, order_id: str) -> Order | None:
        """Получить ордер по ID — НЕ РЕАЛИЗОВАНО (live отключён)."""
        raise NotImplementedError(_LIVE_DISABLED_MSG)

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        """Получить открытые ордера — НЕ РЕАЛИЗОВАНО (live отключён)."""
        raise NotImplementedError(_LIVE_DISABLED_MSG)

    async def get_order_history(
        self,
        symbol: str,
        since: int | None = None,
        limit: int = 100,
    ) -> list[Order]:
        """Получить историю ордеров — НЕ РЕАЛИЗОВАНО (live отключён)."""
        raise NotImplementedError(_LIVE_DISABLED_MSG)

    # === Позиции ===

    async def get_positions(self) -> list[Position]:
        """Открытые биржевые позиции — НЕ РЕАЛИЗОВАНО (live отключён)."""
        raise NotImplementedError(_LIVE_DISABLED_MSG)

    async def close_position(
        self,
        symbol: str,
        quantity: Decimal | None = None,
        price: Decimal | None = None,
    ) -> bool:
        """Закрыть биржевую позицию — НЕ РЕАЛИЗОВАНО (live отключён)."""
        raise NotImplementedError(_LIVE_DISABLED_MSG)

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
