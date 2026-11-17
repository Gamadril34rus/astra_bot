from __future__ import annotations

import pytest
from astra_bot.adapters.bingx.client_live import LiveBingXClient
from astra_bot.core.exceptions import ExchangeError


def _client() -> LiveBingXClient:
    return LiveBingXClient({
        "base_url": "https://open-api-vst.bingx.com",
        "api_key": "test-key",
        "api_secret": "test-secret",
    })


@pytest.mark.asyncio
async def test_get_position_history_parses_nested_response_and_params(monkeypatch):
    client = _client()
    seen = {}

    async def fake_request(method, endpoint, *, params, signed):
        seen.update(method=method, endpoint=endpoint, params=params, signed=signed)
        return {
            "code": 0,
            "data": {
                "positionHistory": [
                    {
                        "symbol": "BTC-USDT",
                        "positionSide": "LONG",
                        "openTime": 1_700_000_000_000,
                        "avgClosePrice": "101.25",
                        "netProfit": "2.5",
                    }
                ]
            },
        }

    monkeypatch.setattr(client, "_request", fake_request)
    rows = await client.get_position_history(
        "BTC-USDT",
        start_ms=1_700_000_000_000,
        end_ms=1_700_000_060_000,
        page_index=0,
        page_size=200,
    )

    assert len(rows) == 1
    assert rows[0]["netProfit"] == "2.5"
    assert seen["method"] == "GET"
    assert seen["endpoint"] == "/openApi/swap/v1/trade/positionHistory"
    assert seen["signed"] is True
    assert seen["params"]["symbol"] == "BTC-USDT"
    assert seen["params"]["startTs"] == 1_700_000_000_000
    assert seen["params"]["endTs"] == 1_700_000_060_000
    assert seen["params"]["pageIndex"] == 0
    assert seen["params"]["pageSize"] == 100


@pytest.mark.asyncio
async def test_get_position_history_accepts_direct_list_response(monkeypatch):
    client = _client()

    async def fake_request(*args, **kwargs):
        return {"code": "0", "data": [{"symbol": "ETH-USDT", "netProfit": "-3.0"}]}

    monkeypatch.setattr(client, "_request", fake_request)
    rows = await client.get_position_history(
        "ETH-USDT",
        start_ms=1_700_000_000_000,
        end_ms=1_700_000_060_000,
    )
    assert rows == [{"symbol": "ETH-USDT", "netProfit": "-3.0"}]


@pytest.mark.asyncio
async def test_get_position_history_rejects_exchange_error(monkeypatch):
    client = _client()

    async def fake_request(*args, **kwargs):
        return {"code": 100410, "msg": "Please try again later"}

    monkeypatch.setattr(client, "_request", fake_request)
    with pytest.raises(ExchangeError, match="position history failed"):
        await client.get_position_history(
            "ETH-USDT",
            start_ms=1_700_000_000_000,
            end_ms=1_700_000_060_000,
        )


@pytest.mark.asyncio
async def test_get_position_history_skips_invalid_range(monkeypatch):
    client = _client()
    calls = 0

    async def fake_request(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"code": 0, "data": []}

    monkeypatch.setattr(client, "_request", fake_request)
    assert await client.get_position_history("BTC-USDT", start_ms=0, end_ms=10) == []
    assert calls == 0
