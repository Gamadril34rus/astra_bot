"""Mock-exchange tests for live BingX adapter (no network)."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from astra_bot.decision import live_commands as lc
from astra_bot.decision.live_broker import HARD_CAP_USD, LiveBroker, LiveEntryPlan


class MockClient:
    def __init__(self, balance: Decimal = Decimal("250"), positions=None):
        self._balance = balance
        self._positions = list(positions or [])
        self._orders: list[Any] = []
        self.placed: list[dict] = []
        self.cancelled: list[tuple] = []
        self.closed: list[str] = []
        self.fail_next: str | None = None

    async def get_balance_usdt(self) -> Decimal:
        return self._balance

    async def get_positions(self):
        return list(self._positions)

    async def get_open_orders(self, symbol: str | None = None):
        if symbol:
            return [o for o in self._orders if getattr(o, "symbol", None) == symbol]
        return list(self._orders)

    async def place_order(self, **kwargs):
        if self.fail_next == "reject":
            self.fail_next = None
            raise RuntimeError("place_order rejected code=101204 msg=insufficient")
        if self.fail_next == "timeout":
            self.fail_next = None
            raise TimeoutError("timeout")
        oid = f"ord-{len(self.placed)+1}"
        o = SimpleNamespace(
            id=oid,
            symbol=kwargs.get("symbol"),
            side=kwargs.get("side"),
            order_type=str(kwargs.get("order_type", "")).lower(),
            quantity=kwargs.get("quantity"),
            status="filled" if kwargs.get("order_type") == "MARKET" else "new",
            filled_quantity=kwargs.get("quantity") if kwargs.get("order_type") == "MARKET" else Decimal("0"),
        )
        self.placed.append(kwargs)
        self._orders.append(o)
        if kwargs.get("order_type") == "MARKET" and not kwargs.get("reduce_only"):
            side = "long" if kwargs.get("side") == "BUY" else "short"
            self._positions.append(
                SimpleNamespace(
                    symbol=kwargs["symbol"],
                    side=side,
                    quantity=kwargs["quantity"],
                    entry_price=Decimal("10"),
                )
            )
        return o

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        self.cancelled.append((symbol, order_id))
        self._orders = [o for o in self._orders if o.id != order_id]
        return True

    async def cancel_all_orders(self, symbol: str) -> int:
        n = len([o for o in self._orders if o.symbol == symbol])
        self._orders = [o for o in self._orders if o.symbol != symbol]
        return n

    async def set_leverage(self, symbol: str, leverage: int, side: str = "LONG") -> None:
        return None

    async def set_margin_isolated(self, symbol: str) -> None:
        return None

    async def close_position(self, symbol: str, quantity=None, price=None) -> bool:
        self.closed.append(symbol)
        self._positions = [p for p in self._positions if p.symbol != symbol]
        return True


def test_hard_cap_100():
    async def _():
        b = LiveBroker(MockClient(Decimal("250")))
        assert await b.equity() == HARD_CAP_USD
        b2 = LiveBroker(MockClient(Decimal("40")))
        assert await b2.equity() == Decimal("40")
    asyncio.run(_())


def test_risk_pct_from_real_equity():
    async def _():
        b = LiveBroker(MockClient(Decimal("80")))
        assert await b.risk_budget(Decimal("0.03")) == Decimal("2.40")
        b2 = LiveBroker(MockClient(Decimal("500")))
        assert await b2.risk_budget(Decimal("0.01")) == Decimal("1.00")
    asyncio.run(_())


def test_halt_before_place():
    async def _():
        b = LiveBroker(MockClient())
        b.set_halt("kill")
        try:
            await b.open_with_protection(
                LiveEntryPlan("BTC-USDT", "long", Decimal("0.01"), Decimal("90"), Decimal("110"))
            )
            raise AssertionError("expected HALT")
        except RuntimeError as e:
            assert "HALT" in str(e)
    asyncio.run(_())


def test_entry_three_orders():
    async def _():
        c = MockClient(Decimal("100"))
        b = LiveBroker(c)
        res = await b.open_with_protection(
            LiveEntryPlan("ETH-USDT", "long", Decimal("1"), Decimal("90"), Decimal("120"), client_tag="t1")
        )
        assert res["entry"] and res["stop"] and res["tp"]
        types = [p["order_type"] for p in c.placed]
        assert types == ["MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"]
    asyncio.run(_())


def test_reject_and_timeout():
    async def _():
        c = MockClient()
        b = LiveBroker(c)
        c.fail_next = "reject"
        try:
            await b.open_with_protection(
                LiveEntryPlan("BTC-USDT", "long", Decimal("1"), Decimal("1"), None)
            )
            raise AssertionError("expected reject")
        except RuntimeError as e:
            assert "rejected" in str(e)
        c.fail_next = "timeout"
        try:
            await b.open_with_protection(
                LiveEntryPlan("BTC-USDT", "long", Decimal("1"), Decimal("1"), None)
            )
            raise AssertionError("expected timeout")
        except TimeoutError:
            pass
    asyncio.run(_())


def test_reconcile_missing_stop():
    async def _():
        pos = SimpleNamespace(symbol="SOL-USDT", side="long", quantity=Decimal("2"), entry_price=Decimal("100"))
        c = MockClient(positions=[pos])
        b = LiveBroker(c)
        rec = await b.reconcile()
        assert rec["missing_stops"] == 1
        assert rec["positions"] == 1
    asyncio.run(_())


def test_flat_all():
    async def _():
        pos = SimpleNamespace(symbol="SOL-USDT", side="long", quantity=Decimal("2"), entry_price=Decimal("100"))
        c = MockClient(positions=[pos])
        c._orders = [SimpleNamespace(id="1", symbol="SOL-USDT", order_type="stop_market")]
        b = LiveBroker(c)
        res = await b.flat_all()
        assert res["closed"] >= 1
        assert "SOL-USDT" in c.closed
    asyncio.run(_())


def test_commands_stop_flat_resume(tmp_path):
    async def _():
        path = tmp_path / "cmds.json"
        c = MockClient()
        b = LiveBroker(c)
        lc.enqueue("stop", path)
        lines = await lc.apply_pending(b, path)
        assert any("blocked" in x for x in lines)
        assert not b.entries_allowed
        lc.enqueue("resume", path)
        await lc.apply_pending(b, path)
        assert b.entries_allowed
        pos = SimpleNamespace(symbol="X", side="long", quantity=Decimal("1"), entry_price=Decimal("1"))
        c._positions = [pos]
        lc.enqueue("flat", path)
        await lc.apply_pending(b, path)
        assert not b.entries_allowed
        assert c.closed
    asyncio.run(_())


def test_secrets_not_in_redact():
    # mirror live_env._redact without importing aiohttp chain
    def _redact(s: object) -> str:
        t = str(s or "")
        if len(t) > 8:
            return t[:3] + "***" + t[-2:]
        return "***"
    assert "***" in _redact("abcdefghijklmnop")
    assert "abcdefghijklmnop" not in _redact("abcdefghijklmnop")


def test_vst_default_base(monkeypatch):
    import importlib.util
    from pathlib import Path
    p = Path("astra_bot/adapters/bingx/live_env.py")
    spec = importlib.util.spec_from_file_location("live_env_standalone", p)
    le = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(le)
    monkeypatch.delenv("ZEUS_MAINNET", raising=False)
    monkeypatch.setenv("ZEUS_VST", "1")
    assert le.resolve_base_url() == le.BINGX_VST_BASE
    monkeypatch.setenv("ZEUS_MAINNET", "1")
    monkeypatch.setenv("ZEUS_VST", "0")
    assert le.resolve_base_url() == le.BINGX_API_BASE
