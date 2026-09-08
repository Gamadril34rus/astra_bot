"""Контрактные тесты BingXClient на подменённом aiohttp-ответе.

Проверяют парсинг ответов BingX USDT-M perps (code=0, data может быть
списком или словарём), подпись у приватных запросов и отключённый live.
"""

from decimal import Decimal

import pytest
from astra_bot.adapters.bingx.client import (
    BINGX_API_BASE,
    BINGX_ENDPOINTS,
    BingXClient,
)
from astra_bot.core.exceptions import ExchangeError


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._payload


class FakeSession:
    """aiohttp.ClientSession с заранее заданными ответами по URL-префиксу."""

    def __init__(self, responses: list[tuple[str, dict]]):
        self._responses = responses
        self.closed = False
        self.last_url = ""
        self.last_headers: dict | None = None

    def _match(self, url: str):
        for prefix, payload in self._responses:
            if str(url).startswith(prefix):
                return payload
        raise AssertionError(f"Unexpected request: {url}")

    def get(self, url, *args, **kwargs):
        self.last_url = str(url)
        self.last_headers = kwargs.get("headers")
        return FakeResponse(self._match(url))

    def post(self, url, *args, **kwargs):
        self.last_url = str(url)
        self.last_headers = kwargs.get("headers")
        return FakeResponse(self._match(url))

    async def close(self):
        self.closed = True


def _common_fixtures() -> list[tuple[str, dict]]:
    return [
        (
            BINGX_API_BASE + BINGX_ENDPOINTS["swap"]["orderbook"],
            {
                "code": 0,
                "data": {
                    "bids": [["49999.0", "0.1"], ["49998.5", "0.3"]],
                    "asks": [["50001.5", "0.2"], ["50002.0", "0.4"]],
                },
            },
        ),
        (
            BINGX_API_BASE + BINGX_ENDPOINTS["swap"]["ticker_24hr"],
            {
                "code": 0,
                "data": [
                    {
                        "symbol": "BTC-USDT",
                        "lastPrice": "50000.5",
                        "bidPrice": "50000.0",
                        "askPrice": "50001.0",
                        "highPrice": "51000",
                        "lowPrice": "49000",
                        "volume": "100",
                        "quoteVolume": "5000000",
                        "priceChange": "0.05",
                    }
                ],
            },
        ),
        (
            BINGX_API_BASE + BINGX_ENDPOINTS["swap"]["candles"],
            {
                "code": 0,
                "data": [
                    [1700000000000, "50000", "50100", "49900", "50050", "10"],
                    [1700000060000, "50050", "50150", "50000", "50100", "12"],
                ],
            },
        ),
        (
            BINGX_API_BASE + BINGX_ENDPOINTS["swap"]["instruments"],
            {
                "code": 0,
                "data": [
                    {
                        "symbol": "BTC-USDT",
                        "currency": "BTC",
                        "tradeCurrency": "USDT",
                        "status": 1,
                        "minQty": "0.001",
                        "minNotional": "5",
                        "tickSize": "0.1",
                        "stepSize": "0.001",
                        "maxLeverage": 125,
                    }
                ],
            },
        ),
        (
            BINGX_API_BASE + BINGX_ENDPOINTS["swap"]["premium_index"],
            {
                "code": 0,
                "data": {
                    "symbol": "BTC-USDT",
                    "markPrice": "50010.5",
                    "indexPrice": "50009.0",
                    "lastFundingRate": "0.0001",
                    "nextFundingTime": 1700000400000,
                },
            },
        ),
        (
            BINGX_API_BASE + BINGX_ENDPOINTS["swap"]["open_interest"],
            {
                "code": 0,
                "data": {"symbol": "BTC-USDT", "openInterest": "1234.5", "time": 1700000000000},
            },
        ),
        (
            BINGX_API_BASE + BINGX_ENDPOINTS["swap"]["funding_rate"],
            {
                "code": 0,
                "data": [
                    {"symbol": "BTC-USDT", "fundingRate": "0.0001", "fundingTime": 1699996800000},
                    {"symbol": "BTC-USDT", "fundingRate": "-0.00005", "fundingTime": 1699968000000},
                ],
            },
        ),
    ]


@pytest.fixture()
def fake_session():
    return FakeSession(_common_fixtures())


@pytest.fixture()
async def client(fake_session):
    config = {
        "api_key": "",
        "api_secret": "",
        "enabled": True,
        "rate_limit_qps": 0,
    }
    c = BingXClient(config)
    await c.initialize()
    c._session = fake_session
    yield c
    c._session = None
    await c.close()


async def test_get_orderbook_parses_data_dict(client):
    book = await client.get_orderbook("BTC-USDT", depth=2)

    assert book.exchange == "bingx"
    assert book.symbol == "BTC-USDT"
    assert len(book.asks) == 2
    assert len(book.bids) == 2
    assert book.asks[0].price < book.asks[1].price
    assert book.bids[0].price > book.bids[1].price
    assert book.best_bid == Decimal("49999.0")
    assert book.best_ask == Decimal("50001.5")


async def test_get_ticker_maps_lastprice_to_last(client):
    ticker = await client.get_ticker("BTC-USDT")
    assert ticker["last"] == Decimal("50000.5")
    assert ticker["bid"] == Decimal("50000.0")
    assert ticker["ask"] == Decimal("50001.0")
    assert ticker["high_24h"] == Decimal("51000")


async def test_get_candles_sorted_ascending(client):
    candles = await client.get_candles("BTC-USDT", "1m", limit=2)
    assert len(candles) == 2
    assert candles[0].open_time < candles[1].open_time
    c = candles[1]
    assert c.open == Decimal("50050")
    assert c.close == Decimal("50100")
    assert c.volume == Decimal("12")
    assert c.exchange == "bingx"


async def test_get_candles_accepts_object_form():
    session = FakeSession(
        [
            (
                BINGX_API_BASE + BINGX_ENDPOINTS["swap"]["candles"],
                {
                    "code": 0,
                    "data": [
                        {
                            "open": "19396.8",
                            "close": "19394.4",
                            "high": "19397.5",
                            "low": "19385.7",
                            "volume": "110.05",
                            "time": 1666583700000,
                        }
                    ],
                },
            )
        ]
    )
    c = BingXClient({"enabled": True, "rate_limit_qps": 0})
    await c.initialize()
    c._session = session
    try:
        candles = await c.get_candles("BTC-USDT", "1h", limit=1)
        assert len(candles) == 1
        assert candles[0].open_time == 1666583700000
        assert candles[0].close == Decimal("19394.4")
    finally:
        c._session = None
        await c.close()


async def test_get_instruments_parses_swap_contracts(client):
    instruments = await client.get_instruments()
    assert len(instruments) == 1
    inst = instruments[0]
    assert inst.symbol == "BTC-USDT"
    assert inst.exchange == "bingx"
    assert inst.base_asset == "BTC"
    assert inst.quote_asset == "USDT"
    assert inst.trading_status == "trading"
    assert inst.min_notional == Decimal("5")
    assert inst.contract_type == "linear"
    assert inst.fee_rate == Decimal("0.0005")


async def test_get_mark_and_funding_from_premium_index(client):
    mark, rate = await client.get_mark_and_funding("BTC-USDT")
    assert mark == Decimal("50010.5")
    assert rate == Decimal("0.0001")
    assert await client.get_mark_price("BTC-USDT") == Decimal("50010.5")
    funding = await client.get_funding_rate("BTC-USDT")
    assert funding["rate"] == Decimal("0.0001")
    assert funding["next_funding_time_ms"] == 1700000400000


async def test_get_open_interest(client):
    assert await client.get_open_interest("BTC-USDT") == Decimal("1234.5")


async def test_get_funding_rate_history_sorted(client):
    hist = await client.get_funding_rate_history("BTC-USDT")
    assert len(hist) == 2
    assert hist[0]["funding_time_ms"] < hist[1]["funding_time_ms"]
    assert hist[1]["rate"] == Decimal("0.0001")


async def test_private_balance_request_is_signed():
    session = FakeSession(
        [
            (
                BINGX_API_BASE + BINGX_ENDPOINTS["swap"]["account"],
                {
                    "code": 0,
                    "data": {
                        "balance": {
                            "userId": "12345",
                            "asset": "USDT",
                            "balance": "100.0",
                            "equity": "105.5",
                            "unrealizedProfit": "5.5",
                            "realisedProfit": "0.0",
                            "availableMargin": "80.0",
                            "usedMargin": "20.0",
                            "freezedMargin": "5.5",
                        }
                    },
                },
            )
        ]
    )
    c = BingXClient(
        {"api_key": "key123", "api_secret": "sec456", "rate_limit_qps": 0}
    )
    await c.initialize()
    c._session = session
    try:
        bals = await c.get_account_balance()
        assert bals["USDT"].free == Decimal("80.0")
        assert bals["USDT"].total == Decimal("105.5")
        assert bals["USDT"].locked == Decimal("25.5")
        # Подпись в query: timestamp + signature; ключ — заголовком.
        assert "timestamp=" in session.last_url
        assert "signature=" in session.last_url
        assert session.last_headers.get("X-BX-APIKEY") == "key123"
    finally:
        c._session = None
        await c.close()


async def test_private_balance_without_keys_returns_empty():
    c = BingXClient({"enabled": True, "rate_limit_qps": 0})
    await c.initialize()
    try:
        assert await c.get_account_balance() == {}
    finally:
        await c.close()


async def test_funding_balance_is_empty_for_bingx(client):
    assert await client.get_funding_balance() == {}


async def test_live_trading_is_disabled(client):
    """Ордера/позиции на бирже недоступны: live отключён, только paper."""
    with pytest.raises(NotImplementedError, match="Live-торговля отключена"):
        await client.place_order("BTC-USDT", "buy", "market", Decimal("1"))
    with pytest.raises(NotImplementedError, match="Live-торговля отключена"):
        await client.cancel_order("BTC-USDT", "123")
    with pytest.raises(NotImplementedError, match="Live-торговля отключена"):
        await client.get_open_orders("BTC-USDT")
    with pytest.raises(NotImplementedError, match="Live-торговля отключена"):
        await client.get_positions()
    with pytest.raises(NotImplementedError, match="Live-торговля отключена"):
        await client.close_position("BTC-USDT")


async def test_api_error_surfaces_from_low_level_request():
    session = FakeSession(
        [
            (
                BINGX_API_BASE + BINGX_ENDPOINTS["swap"]["ticker_24hr"],
                {"code": 100001, "msg": "signature verification failed", "data": {}},
            )
        ]
    )
    c = BingXClient({"enabled": True, "rate_limit_qps": 0})
    await c.initialize()
    c._session = session
    try:
        # Низкоуровневый _request поднимает ExchangeError на code != 0.
        with pytest.raises(ExchangeError, match="signature verification failed"):
            await c._request(
                "GET",
                BINGX_ENDPOINTS["swap"]["ticker_24hr"],
                params={"symbol": "BTC-USDT"},
            )
    finally:
        c._session = None
        await c.close()


async def test_public_endpoint_degrades_on_api_error():
    # Высокоуровневые методы не валят бота: get_ticker возвращает {}.
    session = FakeSession(
        [
            (
                BINGX_API_BASE + BINGX_ENDPOINTS["swap"]["ticker_24hr"],
                {"code": 100400, "msg": "invalid parameter", "data": {}},
            )
        ]
    )
    c = BingXClient({"enabled": True, "rate_limit_qps": 0})
    await c.initialize()
    c._session = session
    try:
        assert await c.get_ticker("BTC-USDT") == {}
    finally:
        c._session = None
        await c.close()


async def test_public_endpoint_degrades_on_network_error():
    # get_ticker оборачивает сетевые ошибки в пустой словарь (degraded-режим).
    c = BingXClient({"enabled": True, "rate_limit_qps": 0})
    # _session не инициализирован — метод вернёт {} вместо исключения.
    assert await c.get_ticker("BTC-USDT") == {}
    assert await c.get_candles("BTC-USDT", "1h") == []
    assert await c.get_mark_price("BTC-USDT") is None
    assert await c.get_open_interest("BTC-USDT") is None


async def test_signed_request_without_keys_is_degraded():
    # Без ключей приватные методы не падают, а возвращают пустоту —
    # контур работает на публичных данных.
    c = BingXClient({"api_key": "", "api_secret": "", "rate_limit_qps": 0})
    await c.initialize()
    try:
        assert await c.get_account_balance() == {}
    finally:
        await c.close()
