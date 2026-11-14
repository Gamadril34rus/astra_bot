from __future__ import annotations

import pytest

from astra_bot.core.exceptions import ExchangeError
from astra_bot.utils.retry import asyncio as retry_asyncio, retry_async


@pytest.mark.asyncio
async def test_position_sl_already_exists_is_not_retried(monkeypatch):
    calls = 0
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(retry_asyncio, "sleep", fake_sleep)

    @retry_async(attempts=3, delays=(2.0, 5.0, 15.0), log_errors=False)
    async def place_stop():
        nonlocal calls
        calls += 1
        raise ExchangeError(
            "BingX API error: Position SL order already exists",
            exchange="bingx",
            operation="/openApi/swap/v2/trade/order",
            code=10406,
        )

    with pytest.raises(ExchangeError, match="Position SL order already exists"):
        await place_stop()

    assert calls == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_transient_exchange_errors_still_use_retry_backoff(monkeypatch):
    calls = 0
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(retry_asyncio, "sleep", fake_sleep)

    @retry_async(attempts=3, delays=(2.0, 5.0, 15.0), log_errors=False)
    async def transient_request():
        nonlocal calls
        calls += 1
        raise ExchangeError(
            "BingX API error: temporary exchange failure",
            exchange="bingx",
            operation="/openApi/swap/v2/trade/order",
            code=50001,
        )

    with pytest.raises(ExchangeError, match="temporary exchange failure"):
        await transient_request()

    assert calls == 3
    assert len(sleeps) == 2
