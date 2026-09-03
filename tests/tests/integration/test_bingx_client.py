"""Контрактные тесты BingXClient на подменённом aiohttp-ответе.

Проверяют парсинг ответов BingX spot (code=0, data может быть списком
или словарём) и наличие подписи у приватных запросов.
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
            BINGX_API_BASE + BINGX_ENDPOINTS["spot"]["orderbook"],
            {
                "code": 0,
                "data": {
                    "bids": [["49999.0", "0.1"], ["49998.5", "0.3"]],
                    "asks": [["50001.5", "0.2"], ["50002.0", "0.4"]],
                },
            },
        ),
        (
            BINGX_API_BASE + BINGX_ENDPOINTS["spot"]["ticker_24hr"],
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
            BINGX_API_BASE + BINGX_ENDPOINTS["spot"]["candles"],
            {
                "code": 0,
                "data": [
                    [1700000000000, "50000", "50100", "49900", "50050", "10"],
                    [1700000060000, "50050", "50150", "50000", "50100", "12"],
                ],
            },
        ),
        (
            BINGX_API_BASE + BINGX_ENDPOINTS["spot"]["instruments"],
            {
                "code": 0,
                "data": {
                    "symbols": [
                        {
                            "symbol": "BTC-USDT",
                            "currency": "BTC",
                            "tradeCurrency": "USDT",
                            "status": 1,
                            "apiStateBuy": True,
                            "apiStateSell": True,
                            "minQty": 0.0001,
                            "minNotional": 5,
                            "tickSize": 0.1,
                            "stepSize": 0.00001,
                        }
                    ]
                },
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
                BINGX_API_BASE + BINGX_ENDPOINTS["spot"]["candles"],
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


async def test_get_instruments_parses_symbols(client):
    instruments = await client.get_instruments()
    assert len(instruments) == 1
    inst = instruments[0]
    assert inst.symbol == "BTC-USDT"
    assert inst.exchange == "bingx"
    assert inst.base_asset == "BTC"
    assert inst.quote_asset == "USDT"
    assert inst.trading_status == "trading"
    assert inst.min_notional == Decimal("5")


async def test_private_balance_request_is_signed():
    session = FakeSession(
        [
            (
                BINGX_API_BASE + BINGX_ENDPOINTS["spot"]["account"],
                {
                    "code": 0,
                    "data": {
                        "balances": [
                            {"asset": "USDT", "free": "45.73", "locked": "0"},
                            {"asset": "BTC", "free": "0.01", "locked": "0.5"},
                        ]
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
        assert bals["USDT"].free == Decimal("45.73")
        assert bals["BTC"].total == Decimal("0.51")
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


async def test_api_error_surfaces_from_low_level_request():
    session = FakeSession(
        [
            (
                BINGX_API_BASE + BINGX_ENDPOINTS["spot"]["ticker_24hr"],
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
                BINGX_ENDPOINTS["spot"]["ticker_24hr"],
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
                BINGX_API_BASE + BINGX_ENDPOINTS["spot"]["ticker_24hr"],
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


async def test_signed_request_without_keys_is_degraded():
    # Без ключей приватные методы не падают, а возвращают пустоту —
    # контур работает на публичных данных.
    c = BingXClient({"api_key": "", "api_secret": "", "rate_limit_qps": 0})
    await c.initialize()
    try:
        assert await c.get_account_balance() == {}
    finally:
        await c.close()
