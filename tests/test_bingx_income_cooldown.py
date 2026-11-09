"""BingX income endpoint cooldown and request-scope regression tests."""
from __future__ import annotations

import asyncio

from astra_bot.adapters.bingx.client_live import (
    LiveBingXClient,
    _income_cooldown_until,
)
from astra_bot.core.exceptions import ExchangeError
from astra_bot.decision.live_income_report import (
    fetch_income_with_retries,
    process_income_fill_tape,
)
from astra_bot.utils.retry import retry_async


def test_retry_async_does_not_retry_structured_bingx_100410() -> None:
    calls = 0

    @retry_async(attempts=3, delays=(0.0, 0.0), log_errors=False)
    async def request() -> None:
        nonlocal calls
        calls += 1
        raise ExchangeError("BingX API error: rate limited", code=100410)

    try:
        asyncio.run(request())
    except ExchangeError as exc:
        assert exc.code == 100410
    else:
        raise AssertionError("BingX 100410 must propagate to the cooldown handler")

    assert calls == 1


def test_income_cooldown_is_cached_and_skips_remaining_requests(monkeypatch) -> None:
    client = object.__new__(LiveBingXClient)
    client._income_cooldown_until = 0.0
    calls: list[str] = []

    async def fake_request(method, endpoint, params=None, signed=False):
        calls.append(params["incomeType"])
        raise ExchangeError("BingX API error: rate limited", code=100410)

    monkeypatch.setattr(client, "_request", fake_request)

    async def run() -> None:
        assert await client.get_realized_income("BTC-USDT") == []
        assert client.income_endpoint_cooldown_active
        assert await client.get_realized_income("ETH-USDT") == []

    asyncio.run(run())
    assert calls == ["REALIZED_PNL"]


def test_income_cooldown_parses_millisecond_epoch() -> None:
    deadline = _income_cooldown_until(
        ExchangeError(
            "BingX endpoint disabled; unblocked after 1700000600000",
            code=100410,
        ),
        now=1_700_000_000.0,
    )
    assert deadline == 1_700_000_600.0


def test_income_cooldown_bounds_implausible_expiry() -> None:
    deadline = _income_cooldown_until(
        ExchangeError(
            "BingX endpoint disabled; unblocked after 4102444800000",
            code=100410,
        ),
        now=1_700_000_000.0,
    )
    assert deadline == 1_700_000_900.0


def test_income_client_can_request_one_type(monkeypatch) -> None:
    client = object.__new__(LiveBingXClient)
    client._income_cooldown_until = 0.0
    calls: list[str] = []

    async def fake_request(method, endpoint, params=None, signed=False):
        calls.append(params["incomeType"])
        return {"code": 0, "data": [{"income": "0.25", "time": 1_700_000_000_000}]}

    monkeypatch.setattr(client, "_request", fake_request)
    rows = asyncio.run(
        client.get_realized_income("BTC-USDT", income_types=("FUNDING_FEE",))
    )

    assert calls == ["FUNDING_FEE"]
    assert rows[0]["_incomeType"] == "FUNDING_FEE"


def test_income_retry_loop_stops_when_client_is_in_cooldown() -> None:
    class Client:
        income_endpoint_cooldown_active = False

        def __init__(self) -> None:
            self.calls = 0

        async def get_realized_income(self, symbol, start_ms=None, end_ms=None):
            self.calls += 1
            self.income_endpoint_cooldown_active = True
            return []

    client = Client()
    result = asyncio.run(
        fetch_income_with_retries(
            client,
            "BTC-USDT",
            1,
            attempts=3,
            delay_sec=0,
        )
    )

    assert client.calls == 1
    assert result["has_realized"] is False


def test_income_fill_tape_requests_only_realized_pnl(tmp_path) -> None:
    class Client:
        def __init__(self) -> None:
            self.requested_types: list[tuple[str, ...] | None] = []

        async def get_realized_income(
            self,
            symbol,
            *,
            start_ms=None,
            end_ms=None,
            income_types=None,
        ):
            self.requested_types.append(income_types)
            return []

    client = Client()
    result = asyncio.run(
        process_income_fill_tape(
            client,
            ["BTC-USDT"],
            state_path=tmp_path / "seen.json",
            trades_path=tmp_path / "trades.jsonl",
        )
    )

    assert client.requested_types == [("REALIZED_PNL",)]
    assert result["scanned"] == 0
    assert result["written"] == 0
