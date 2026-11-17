from __future__ import annotations

import pytest
from astra_bot.adapters.bingx.client_live import LiveBingXClient
from astra_bot.core.exceptions import ExchangeError


@pytest.mark.asyncio
async def test_get_positions_coalesces_reads_and_market_order_invalidates_cache(monkeypatch):
    client = LiveBingXClient({
        "base_url": "https://open-api-vst.bingx.com",
        "api_key": "test-key",
        "api_secret": "test-secret",
    })
    calls = 0

    async def fake_request(method, endpoint, *args, **kwargs):
        nonlocal calls
        calls += 1
        if method == "POST":
            return {
                "data": {
                    "orderId": "1",
                    "symbol": "BTC-USDT",
                    "side": "BUY",
                    "type": "MARKET",
                    "status": "FILLED",
                    "executedQty": "1",
                    "avgPrice": "1",
                }
            }
        return {"data": []}

    monkeypatch.setattr(client, "_request", fake_request)
    assert await client.get_positions() == []
    assert await client.get_positions() == []
    assert calls == 1

    client._invalidate_positions_cache()
    assert await client.get_positions() == []
    assert calls == 2

    client._positions_cache_until = 1e20
    await client.place_order(
        symbol="BTC-USDT",
        side="BUY",
        order_type="MARKET",
        quantity=1,
        position_side="LONG",
    )
    assert client._positions_cache_until == 0.0


@pytest.mark.asyncio
async def test_get_positions_100410_avoids_repeated_endpoint_calls(monkeypatch):
    client = LiveBingXClient({
        "base_url": "https://open-api-vst.bingx.com",
        "api_key": "test-key",
        "api_secret": "test-secret",
    })
    calls = 0

    async def fail_100410(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise ExchangeError(
            "BingX API error: Please try again later.",
            exchange="bingx",
            operation="/openApi/swap/v2/user/positions",
            code=100410,
        )

    monkeypatch.setattr(client, "_request", fail_100410)

    with pytest.raises(ExchangeError, match="Please try again later"):
        await client.get_positions()
    assert calls == 1
    assert client.positions_endpoint_cooldown_active

    with pytest.raises(ExchangeError, match="local 100410 cooldown"):
        await client.get_positions()
    assert calls == 1

    client._positions_cooldown_until = 0.0

    async def success_empty(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"data": []}

    monkeypatch.setattr(client, "_request", success_empty)
    assert await client.get_positions() == []
    assert calls == 2
    assert not client.positions_endpoint_cooldown_active
