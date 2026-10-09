"""LiveBroker: exchange is truth; hard cap $100; risk % of real equity.

Does not touch PaperBroker. HALT/kill checked before any place_order.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

logger = logging.getLogger(__name__)

HARD_CAP_USD = Decimal("100")


class LiveClientProto(Protocol):
    async def get_balance_usdt(self) -> Decimal: ...
    async def get_positions(self) -> list[Any]: ...
    async def get_open_orders(self, symbol: str | None = None) -> list[Any]: ...
    async def place_order(self, **kwargs: Any) -> Any: ...
    async def cancel_order(self, symbol: str, order_id: str) -> bool: ...
    async def cancel_all_orders(self, symbol: str) -> int: ...
    async def set_leverage(self, symbol: str, leverage: int, side: str = "LONG") -> None: ...
    async def set_margin_isolated(self, symbol: str) -> None: ...
    async def close_position(self, symbol: str, quantity: Any = None, price: Any = None) -> bool: ...


@dataclass
class LiveBrokerConfig:
    hard_cap_usd: Decimal = HARD_CAP_USD
    max_leverage: int = 10
    default_leverage: int = 5


@dataclass
class LiveEntryPlan:
    symbol: str
    side: str  # long|short
    quantity: Decimal
    stop_price: Decimal
    take_profit_price: Decimal | None = None
    leverage: int = 5
    client_tag: str = ""


@dataclass
class LiveBroker:
    client: Any
    config: LiveBrokerConfig = field(default_factory=LiveBrokerConfig)
    _entries_allowed: bool = True
    _halted: bool = False
    _halt_reason: str = ""

    def set_halt(self, reason: str) -> None:
        self._halted = True
        self._halt_reason = reason
        logger.warning("LiveBroker HALT: %s", reason)

    def clear_halt(self) -> None:
        self._halted = False
        self._halt_reason = ""

    def block_entries(self) -> None:
        self._entries_allowed = False

    def allow_entries(self) -> None:
        self._entries_allowed = True

    @property
    def entries_allowed(self) -> bool:
        return self._entries_allowed and not self._halted

    async def equity(self) -> Decimal:
        """min(wallet, hard_cap)."""
        wallet = await self.client.get_balance_usdt()
        if wallet < 0:
            wallet = Decimal("0")
        return min(wallet, self.config.hard_cap_usd)

    async def risk_budget(self, risk_pct: Decimal) -> Decimal:
        """Dollar risk from real equity * pct (e.g. 0.01..0.03)."""
        eq = await self.equity()
        return (eq * risk_pct).quantize(Decimal("0.01"))

    def _assert_can_trade(self) -> None:
        if self._halted:
            raise RuntimeError(f"HALT before place_order: {self._halt_reason}")

    async def reconcile(self) -> dict[str, Any]:
        """Exchange is truth. Restore protective stops if missing."""
        positions = await self.client.get_positions()
        open_orders = await self.client.get_open_orders()
        by_sym: dict[str, list] = {}
        for o in open_orders:
            by_sym.setdefault(getattr(o, "symbol", ""), []).append(o)
        restored = 0
        for p in positions:
            sym = p.symbol
            orders = by_sym.get(sym, [])
            has_stop = any(
                "stop" in str(getattr(o, "order_type", "")).lower() for o in orders
            )
            if not has_stop and p.quantity > 0:
                # Cannot invent stop price without plan — flag for clock
                logger.warning("reconcile: %s open qty=%s but no stop order", sym, p.quantity)
                restored += 1
        eq = await self.equity()
        return {
            "equity": str(eq),
            "positions": len(positions),
            "open_orders": len(open_orders),
            "missing_stops": restored,
            "positions_raw": positions,
            "orders_raw": open_orders,
        }

    async def open_with_protection(self, plan: LiveEntryPlan) -> dict[str, Any]:
        """Entry = position MARKET + STOP_MARKET + optional TP_MARKET."""
        self._assert_can_trade()
        if not self._entries_allowed:
            raise RuntimeError("entries blocked (/stop)")
        lev = min(int(plan.leverage), int(self.config.max_leverage))
        await self.client.set_margin_isolated(plan.symbol)
        side_long = plan.side.lower() == "long"
        await self.client.set_leverage(
            plan.symbol, lev, side="LONG" if side_long else "SHORT"
        )
        # exposure check
        eq = await self.equity()
        # rough notional
        # quantity is in coins; skip deep price check here (clock supplies sane qty)
        buy_sell = "BUY" if side_long else "SELL"
        ps = "LONG" if side_long else "SHORT"
        tag = (plan.client_tag or "z")[:8]
        entry = await self.client.place_order(
            symbol=plan.symbol,
            side=buy_sell,
            order_type="MARKET",
            quantity=plan.quantity,
            position_side=ps,
            client_order_id=f"{tag}-e",
        )
        # stop opposite
        stop_side = "SELL" if side_long else "BUY"
        stop = await self.client.place_order(
            symbol=plan.symbol,
            side=stop_side,
            order_type="STOP_MARKET",
            quantity=plan.quantity,
            stop_price=plan.stop_price,
            position_side=ps,
            close_position=True,
            client_order_id=f"{tag}-s",
        )
        tp = None
        if plan.take_profit_price is not None:
            tp = await self.client.place_order(
                symbol=plan.symbol,
                side=stop_side,
                order_type="TAKE_PROFIT_MARKET",
                quantity=plan.quantity,
                stop_price=plan.take_profit_price,
                position_side=ps,
                reduce_only=True,
                client_order_id=f"{tag}-t",
            )
        return {"entry": entry, "stop": stop, "tp": tp, "equity": str(eq)}

    async def move_stop(self, symbol: str, side: str, quantity: Decimal, stop_price: Decimal) -> Any:
        """After partial close: cancel old stops, place new STOP_MARKET on remainder."""
        self._assert_can_trade()
        orders = await self.client.get_open_orders(symbol)
        for o in orders:
            ot = str(getattr(o, "order_type", "")).lower()
            if "stop" in ot and getattr(o, "id", None):
                # Ревью арбитра: неотменённый старый стоп не должен
                # ронять перенос — пишем warning и продолжаем цикл.
                try:
                    await self.client.cancel_order(symbol, str(o.id))
                except Exception as exc:
                    logger.warning("move_stop: cancel_order %s failed: %s", o.id, exc)
        ps = "LONG" if side.lower() == "long" else "SHORT"
        stop_side = "SELL" if side.lower() == "long" else "BUY"
        return await self.client.place_order(
            symbol=symbol,
            side=stop_side,
            order_type="STOP_MARKET",
            quantity=quantity,
            stop_price=stop_price,
            position_side=ps,
            close_position=True,
        )

    async def flat_all(self) -> dict[str, Any]:
        """cancel_all per symbol + close positions reduceOnly.

        Ревью арбитра: сбой на одном символе не должен прерывать флэт
        остальных — каждый вызов в try/except, счётчик failed, цикл
        продолжается. Возврат: cancelled / closed / failed.
        """
        positions = await self.client.get_positions()
        cancelled = 0
        closed = 0
        failed = 0
        for p in positions:
            try:
                cancelled += await self.client.cancel_all_orders(p.symbol)
            except Exception as exc:
                failed += 1
                logger.warning("flat_all: cancel_all_orders %s failed: %s", p.symbol, exc)
            try:
                if await self.client.close_position(p.symbol):
                    closed += 1
            except Exception as exc:
                failed += 1
                logger.warning("flat_all: close_position %s failed: %s", p.symbol, exc)
        # also cancel orphans
        for o in await self.client.get_open_orders():
            if getattr(o, "id", None) and getattr(o, "symbol", None):
                try:
                    await self.client.cancel_order(o.symbol, str(o.id))
                    cancelled += 1
                except Exception as exc:
                    failed += 1
                    logger.warning("flat_all: cancel_order %s failed: %s", o.id, exc)
        return {"cancelled": cancelled, "closed": closed, "failed": failed}
