"""Контрактные тесты OKXClient на подменённом aiohttp-ответе."""

from decimal import Decimal

import pytest
from astra_bot.adapters.okx.client import (
    OKX_API_BASE,
    OKX_ENDPOINTS,
    OKXClient,
)


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

    def _match(self, url: str):
        for prefix, payload in self._responses:
            if str(url).startswith(prefix):
                return payload
        raise AssertionError(f"Unexpected request: {url}")

    def get(self, url, *args, **kwargs):
        return FakeResponse(self._match(url))

    def post(self, url, *args, **kwargs):
        return FakeResponse(self._match(url))

    async def close(self):
        self.closed = True


@pytest.fixture()
def fake_session():
    return FakeSession(
        [
            (
                OKX_API_BASE + OKX_ENDPOINTS["spot"]["orderbook"],
                {
                    "code": "0",
                    "data": [
                        {
                            "asks": [["50001.5", "0.2"], ["50002.0", "0.4"]],
                            "bids": [["49999.0", "0.1"], ["49998.5", "0.3"]],
                        }
                    ],
                },
            ),
            (
                OKX_API_BASE + OKX_ENDPOINTS["spot"]["ticker"],
                {
                    "code": "0",
                    "data": [
                        {
                            "last": "50000.5",
                            "bidPx": "50000.0",
                            "askPx": "50001.0",
                            "high24h": "51000",
                            "low24h": "49000",
                            "vol24h": "100",
                            "volCcy24h": "5000000",
                            "pretRcl": "0.05",
                        }
                    ],
                },
            ),
            (
                OKX_API_BASE + OKX_ENDPOINTS["spot"]["candles"],
                {
                    "code": "0",
                    "data": [
                        [
                            "1700000000000",
                            "50000",
                            "50100",
                            "49900",
                            "50050",
                            "10",
                            "500000",
                        ]
                    ],
                },
            ),
        ]
    )


@pytest.fixture()
async def client(fake_session):
    config = {
        "api_key": "",
        "api_secret": "",
        "passphrase": "",
        "sandbox": False,
        "enabled": True,
        "rate_limit_qps": 0,
    }
    c = OKXClient(config)
    await c.initialize()
    c._session = fake_session
    yield c
    await c.close()


async def test_get_orderbook_parses_first_data_element(client):
    book = await client.get_orderbook("BTC-USDT", depth=2)

    assert book.symbol == "BTC-USDT"
    assert len(book.asks) == 2
    assert len(book.bids) == 2
    assert book.asks[0].price < book.asks[1].price
    assert book.bids[0].price > book.bids[1].price
    assert book.best_bid == Decimal("49999.0")
    assert book.best_ask == Decimal("50001.5")


async def test_get_ticker(client):
    ticker = await client.get_ticker("BTC-USDT")
    assert ticker["last"] == Decimal("50000.5")
    assert ticker["bid"] == Decimal("50000.0")
    assert ticker["ask"] == Decimal("50001.0")


async def test_api_error_surfaces_from_low_level_request():
    # get_ticker логирует ошибку и возвращает {} в degraded-режиме, но сам
    # низкоуровневый _request поднимает исключение — это и проверяем.
    session = FakeSession(
        [
            (
                OKX_API_BASE + OKX_ENDPOINTS["spot"]["ticker"],
                {"code": "50011", "msg": "Too Many Requests", "data": []},
            )
        ]
    )
    c = OKXClient({"enabled": True, "rate_limit_qps": 0})
    await c.initialize()
    c._session = session
    try:
        with pytest.raises(Exception, match="Too Many Requests"):
            await c._request(
                "GET", OKX_ENDPOINTS["spot"]["ticker"], params={"instId": "BTC-USDT"}
            )
    finally:
        c._session = None
        await c.close()


async def test_public_endpoint_degrades_to_empty_dict_on_error():
    session = FakeSession(
        [
            (
                OKX_API_BASE + OKX_ENDPOINTS["spot"]["ticker"],
                {"code": "50011", "msg": "Too Many Requests", "data": []},
            )
        ]
    )
    c = OKXClient({"enabled": True, "rate_limit_qps": 0})
    await c.initialize()
    c._session = session
    try:
        assert await c.get_ticker("BTC-USDT") == {}
    finally:
        c._session = None
        await c.close()


async def test_get_candles(client):
    candles = await client.get_candles("BTC-USDT", "1m", limit=1)
    assert len(candles) == 1
    c = candles[0]
    assert c.open == Decimal("50000")
    assert c.high == Decimal("50100")
    assert c.low == Decimal("49900")
    assert c.close == Decimal("50050")
    assert c.volume == Decimal("10")


async def test_uninitialized_session_returns_empty_on_public_endpoint():
    # get_ticker оборачивает сетевые ошибки в пустой словарь, чтобы бот мог
    # работать в degraded-режиме.
    c = OKXClient({"enabled": True, "rate_limit_qps": 0})
    assert await c.get_ticker("BTC-USDT") == {}
