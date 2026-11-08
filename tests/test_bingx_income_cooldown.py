"""BingX income endpoint cooldown and request-scope tests."""
from __future__ import annotations

import asyncio

from astra_bot.adapters.bingx.client_live import LiveBingXClient
from astra_bot.core.exceptions import ExchangeError
from astra_bot.decision.live_income_report import fetch_income_with_retries, process_income_fill_tape
from astra_bot.utils.retry import retry_async


def test_retry_async_does_not_retry_bingx_100410():
    calls = 0

    @retry_async(attempts=3, delays=(0.0, 0.0))
    async def request():
        nonlocal calls
        calls += 1
        raise ExchangeError(
            "BingX API error: code=100410: endpoint trigger frequency limit "
            "is in the disabled period"
        )

    try:
        asyncio.run(request())
    except ExchangeError:
        pass
    else:
        raise AssertionError("100410 must propagate to the cooldown handler")
    assert calls == 1


def test_get_realized_income_sets_cooldown_and_skips_remaining_types(monkeypatch):
    client = object.__new__(LiveBingXClient)
    client._income_cooldown_until = 0.0
    calls: list[str] = []

    async def fake_request(method, endpoint, params=None, signed=False):
        calls.append(params["incomeType"])
        raise ExchangeError(
            "BingX API error: code=100410: endpoint trigger frequency limit "
            "is in the disabled period and will be unblocked after 4102444800000"
        )

    monkeypatch.setattr(client, "_request", fake_request)
    rows = asyncio.run(client.get_realized_income("BTC-USDT"))
    assert rows == []
    assert calls == ["REALIZED_PNL"]
    assert client.income_endpoint_cooldown_active
    assert asyncio.run(client.get_realized_income("ETH-USDT")) == []
    assert calls == ["REALIZED_PNL"]


def test_get_realized_income_can_request_one_type(monkeypatch):
    client = object.__new__(LiveBingXClient)
    client._income_cooldown_until = 0.0
    calls: list[str] = []

    async def fake_request(method, endpoint, params=None, signed=False):
        calls.append(params["incomeType"])
        return {"code": 0, "data": [{"income": "0.25", "time": 1_700_000_000_000}]}

    monkeypatch.setattr(client, "_request", fake_request)
    rows = asyncio.run(
        client.get_realized_income(
            "BTC-USDT", income_types=("FUNDING_FEE",)
        )
    )
    assert calls == ["FUNDING_FEE"]
    assert rows[0]["_incomeType"] == "FUNDING_FEE"


def test_income_retry_stops_after_cooldown_is_set():
    class Client:
        income_endpoint_cooldown_active = False

        def __init__(self):
            self.calls = 0

        async def get_realized_income(self, symbol, start_ms=None, end_ms=None):
            self.calls += 1
            self.income_endpoint_cooldown_active = True
            return []

    client = Client()
    result = asyncio.run(
        fetch_income_with_retries(client, "BTC-USDT", 1, attempts=3, delay_sec=0)
    )
    assert client.calls == 1
    assert result["has_realized"] is False


def test_income_fill_tape_requests_only_realized_pnl(tmp_path):
    class Client:
        async def get_realized_income(
            self, symbol, *, start_ms=None, end_ms=None, income_types=None
        ):
            assert income_types == ("REALIZED_PNL",)
            return []

    result = asyncio.run(
        process_income_fill_tape(
            Client(),
            ["BTC-USDT"],
            state_path=tmp_path / "seen.json",
            trades_path=tmp_path / "trades.jsonl",
        )
    )
    assert result["scanned"] == 0
    assert result["written"] == 0
