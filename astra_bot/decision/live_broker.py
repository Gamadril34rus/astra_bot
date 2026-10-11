"""LiveBroker: exchange is truth; hard cap $100; risk % of real equity.

Does not touch PaperBroker. HALT/kill checked before any place_order.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

HARD_CAP_USD = Decimal("100")

_ORDER_SEQ = 0


def unique_client_order_id(symbol: str, role: str) -> str:
    """BingX forbids repeating clientOrderId (30248). Unique per order.

    Format: arm-<ms>-<seq>-<first4 base>-<role>  role in e|s|t|r …
    """
    global _ORDER_SEQ
    _ORDER_SEQ += 1
    base = (symbol or "X").split("-")[0][:4]
    ms = time.time_ns() // 1_000_000
    return f"arm-{ms}-{_ORDER_SEQ}-{base}-{role}"


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

    async def restore_missing_stops(
        self,
        *,
        positions_path: Path | None = None,
    ) -> dict[str, Any]:
        """After reconcile: positions without stop get plan stop or market close.

        (a) plan in zeus_live_positions.json → re-place STOP_MARKET (+TP) via move_stop
        (b) no plan → market reduceOnly close + warning (naked position forbidden)
        """
        from pathlib import Path as _Path

        from astra_bot.decision.live_positions import get_plan, remove_plan

        path = positions_path or _Path("models/zeus_live_positions.json")
        positions = await self.client.get_positions()
        open_orders = await self.client.get_open_orders()
        by_sym: dict[str, list] = {}
        for o in open_orders:
            by_sym.setdefault(getattr(o, "symbol", ""), []).append(o)

        restored: list[str] = []
        closed: list[str] = []
        for p in positions:
            sym = getattr(p, "symbol", "")
            qty = getattr(p, "quantity", None) or Decimal("0")
            if qty <= 0:
                continue
            orders = by_sym.get(sym, [])
            has_stop = any(
                "stop" in str(getattr(o, "order_type", "")).lower()
                and "take_profit" not in str(getattr(o, "order_type", "")).lower()
                for o in orders
            )
            if has_stop:
                continue
            plan = get_plan(sym, path)
            if plan and plan.get("stop"):
                side = str(plan.get("side") or getattr(p, "side", "long")).lower()
                stop_px = Decimal(str(plan["stop"]))
                q = Decimal(str(plan.get("qty") or qty))
                try:
                    await self.move_stop(sym, side, q, stop_px)
                    tp = plan.get("tp")
                    if tp is not None:
                        stop_side = "SELL" if side == "long" else "BUY"
                        ps = "LONG" if side == "long" else "SHORT"
                        await self.client.place_order(
                            symbol=sym,
                            side=stop_side,
                            order_type="TAKE_PROFIT_MARKET",
                            quantity=q,
                            stop_price=Decimal(str(tp)),
                            position_side=ps,
                            reduce_only=True,
                            client_order_id=unique_client_order_id(symbol, "r"),
                        )
                    restored.append(sym)
                    logger.info("restore_missing_stops: %s STOP re-placed @ %s", sym, stop_px)
                except Exception as exc:
                    logger.warning(
                        "restore_missing_stops: %s move_stop failed: %s",
                        sym, type(exc).__name__,
                    )
            else:
                logger.warning(
                    "restore_missing_stops: %s naked position qty=%s — no plan, closing market",
                    sym, qty,
                )
                try:
                    await self.client.close_position(sym, quantity=qty)
                    remove_plan(sym, path)
                    closed.append(sym)
                except Exception as exc:
                    logger.warning(
                        "restore_missing_stops: close %s failed: %s",
                        sym, type(exc).__name__,
                    )
        return {"restored": restored, "closed_naked": closed}

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
        eq = await self.equity()
        buy_sell = "BUY" if side_long else "SELL"
        ps = "LONG" if side_long else "SHORT"
        entry = await self.client.place_order(
            symbol=plan.symbol,
            side=buy_sell,
            order_type="MARKET",
            quantity=plan.quantity,
            position_side=ps,
            client_order_id=unique_client_order_id(plan.symbol, "e"),
        )
        stop_side = "SELL" if side_long else "BUY"
        stop = await self.client.place_order(
            symbol=plan.symbol,
            side=stop_side,
            order_type="STOP_MARKET",
            quantity=plan.quantity,
            stop_price=plan.stop_price,
            position_side=ps,
            close_position=True,
            client_order_id=unique_client_order_id(plan.symbol, "s"),
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
                client_order_id=unique_client_order_id(plan.symbol, "t"),
            )
        return {"entry": entry, "stop": stop, "tp": tp, "equity": str(eq)}

    async def move_stop(self, symbol: str, side: str, quantity: Decimal, stop_price: Decimal) -> Any:
        """After partial close: cancel old stops, place new STOP_MARKET on remainder."""
        self._assert_can_trade()
        orders = await self.client.get_open_orders(symbol)
        for o in orders:
            ot = str(getattr(o, "order_type", "")).lower()
            if "stop" in ot and getattr(o, "id", None):
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
        """cancel_all per symbol + close positions reduceOnly."""
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
        for o in await self.client.get_open_orders():
            if getattr(o, "id", None) and getattr(o, "symbol", None):
                try:
                    await self.client.cancel_order(o.symbol, str(o.id))
                    cancelled += 1
                except Exception as exc:
                    failed += 1
                    logger.warning("flat_all: cancel_order %s failed: %s", o.id, exc)
        return {"cancelled": cancelled, "closed": closed, "failed": failed}
