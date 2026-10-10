"""BingX swap LIVE client (VST by default). Does not replace BingXClient stubs.

Activate only via LiveBingXClient / LiveBroker. Existing place_order stubs in
client.py stay NotImplementedError (paper path unchanged).
"""
from __future__ import annotations

import logging
import os
import time
from decimal import Decimal
from typing import Any

from astra_bot.adapters.base import Order, Position
from astra_bot.adapters.bingx.client import BINGX_SWAP_V2_PREFIX, BingXClient
from astra_bot.adapters.bingx.live_env import _redact, resolve_base_url

logger = logging.getLogger(__name__)

# Trade endpoints (swap v2)
_TRADE = {
    "order": f"{BINGX_SWAP_V2_PREFIX}/trade/order",
    "cancel": f"{BINGX_SWAP_V2_PREFIX}/trade/order",
    "cancel_all": f"{BINGX_SWAP_V2_PREFIX}/trade/allOpenOrders",
    "open_orders": f"{BINGX_SWAP_V2_PREFIX}/trade/openOrders",
    "positions": f"{BINGX_SWAP_V2_PREFIX}/user/positions",
    "leverage": f"{BINGX_SWAP_V2_PREFIX}/trade/leverage",
    "margin_type": f"{BINGX_SWAP_V2_PREFIX}/trade/marginType",
    "balance_v3": "/openApi/swap/v3/user/balance",
}


def _parse_order(data: dict[str, Any], symbol: str = "") -> Order:
    d = data.get("order") if isinstance(data.get("order"), dict) else data
    if not isinstance(d, dict):
        d = {}
    status_map = {
        "NEW": "new",
        "PENDING": "pending",
        "PARTIALLY_FILLED": "partially_filled",
        "FILLED": "filled",
        "CANCELED": "canceled",
        "CANCELLED": "canceled",
        "REJECTED": "rejected",
        "EXPIRED": "expired",
    }
    st = str(d.get("status") or "new").upper()
    qty = Decimal(str(d.get("quantity") or d.get("origQty") or "0"))
    filled = Decimal(str(d.get("executedQty") or d.get("filledQty") or "0"))
    px = d.get("avgPrice") or d.get("price")
    return Order(
        id=str(d.get("orderId") or d.get("orderID") or "") or None,
        client_order_id=str(d.get("clientOrderId") or "") or None,
        exchange="bingx",
        symbol=str(d.get("symbol") or symbol),
        side=str(d.get("side") or "").lower(),
        order_type=str(d.get("type") or "").lower(),
        quantity=qty,
        price=Decimal(str(px)) if px not in (None, "", "0") else None,
        stop_price=Decimal(str(d["stopPrice"])) if d.get("stopPrice") else None,
        status=status_map.get(st, st.lower()),
        filled_quantity=filled,
        filled_price=Decimal(str(px)) if px not in (None, "", "0") else None,
        exchange_order_id=str(d.get("orderId") or "") or None,
        reject_reason=str(d.get("msg") or "") or None,
    )


class LiveBingXClient(BingXClient):
    """BingX client with live trade methods. Base URL VST unless ZEUS_MAINNET=1."""

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = dict(config or {})
        cfg.setdefault("base_url", resolve_base_url())
        # Prefer live keys; never print them.
        cfg.setdefault("api_key", os.environ.get("BINGX_API_KEY", ""))
        cfg.setdefault("api_secret", os.environ.get("BINGX_API_SECRET", ""))
        super().__init__(cfg)
        self._is_vst = "vst" in (self.base_url or "").lower()
        logger.info(
            "LiveBingXClient base=%s vst=%s key=%s",
            self.base_url,
            self._is_vst,
            _redact(self.api_key),
        )

    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        price: Decimal | None = None,
        stop_price: Decimal | None = None,
        client_order_id: str | None = None,
        *,
        position_side: str | None = None,
        reduce_only: bool = False,
        close_position: bool = False,
        post_only: bool = False,
        time_in_force: str | None = None,
        working_type: str = "MARK_PRICE",
    ) -> Order:
        """MARKET/LIMIT/STOP_MARKET/TAKE_PROFIT_MARKET."""
        if not self.api_key or not self.api_secret:
            raise RuntimeError("BINGX_API_KEY/SECRET required for live orders")
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type.upper(),
            "timestamp": int(time.time() * 1000),
        }
        if quantity is not None:
            params["quantity"] = float(quantity)
        if price is not None:
            params["price"] = float(price)
        if stop_price is not None:
            params["stopPrice"] = float(stop_price)
        if client_order_id:
            params["clientOrderID"] = client_order_id[:40]
        if position_side:
            params["positionSide"] = position_side.upper()
        if reduce_only:
            params["reduceOnly"] = "true"
        if close_position:
            params["closePosition"] = "true"
        tif = time_in_force
        if post_only:
            tif = "PostOnly"
        if tif:
            params["timeInForce"] = tif
        if order_type.upper() in ("STOP_MARKET", "TAKE_PROFIT_MARKET", "STOP", "TAKE_PROFIT"):
            params["workingType"] = working_type
        resp = await self._request("POST", _TRADE["order"], params=params, signed=True)
        code = resp.get("code")
        if code not in (0, "0", None):
            msg = str(resp.get("msg") or "order rejected")
            raise RuntimeError(f"place_order rejected code={code} msg={msg}")
        data = resp.get("data") or resp
        return _parse_order(data if isinstance(data, dict) else {}, symbol)

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        params = {
            "symbol": symbol,
            "orderId": order_id,
            "timestamp": int(time.time() * 1000),
        }
        resp = await self._request("DELETE", _TRADE["cancel"], params=params, signed=True)
        return resp.get("code") in (0, "0", None)

    async def cancel_all_orders(self, symbol: str) -> int:
        params = {"symbol": symbol, "timestamp": int(time.time() * 1000)}
        resp = await self._request("DELETE", _TRADE["cancel_all"], params=params, signed=True)
        if resp.get("code") not in (0, "0", None):
            return 0
        data = resp.get("data")
        if isinstance(data, list):
            return len(data)
        return 1

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        params: dict[str, Any] = {"timestamp": int(time.time() * 1000)}
        if symbol:
            params["symbol"] = symbol
        resp = await self._request("GET", _TRADE["open_orders"], params=params, signed=True)
        data = resp.get("data") or []
        if isinstance(data, dict):
            data = data.get("orders") or []
        out: list[Order] = []
        for item in data or []:
            if isinstance(item, dict):
                out.append(_parse_order(item, symbol or ""))
        return out

    async def get_positions(self) -> list[Position]:
        params = {"timestamp": int(time.time() * 1000)}
        resp = await self._request("GET", _TRADE["positions"], params=params, signed=True)
        data = resp.get("data") or []
        if isinstance(data, dict):
            data = data.get("positions") or data.get("position") or []
        out: list[Position] = []
        for item in data or []:
            if not isinstance(item, dict):
                continue
            amt = Decimal(str(item.get("positionAmt") or item.get("availableAmt") or "0"))
            if amt == 0:
                continue
            side = "long" if amt > 0 else "short"
            ps = str(item.get("positionSide") or "").upper()
            if ps == "SHORT":
                side = "short"
            elif ps == "LONG":
                side = "long"
            out.append(
                Position(
                    exchange="bingx",
                    symbol=str(item.get("symbol") or ""),
                    side=side,
                    quantity=abs(amt),
                    entry_price=Decimal(str(item.get("avgPrice") or item.get("entryPrice") or "0")),
                    current_price=Decimal(str(item.get("markPrice") or "0")) or None,
                    unrealized_pnl=Decimal(str(item.get("unrealizedProfit") or "0")),
                    status="open",
                )
            )
        return out

    async def get_balance_usdt(self) -> Decimal:
        """Wallet equity in USDT or VST (prefer v3 balance).

        Demo (VST) server names the coin "VST", so both assets are accepted.
        If neither is present, fall back to the first row with equity > 0,
        and then to get_account_balance() (which keys balances by asset too).
        """
        try:
            params = {"timestamp": int(time.time() * 1000)}
            resp = await self._request("GET", _TRADE["balance_v3"], params=params, signed=True)
            data = resp.get("data") or {}
            rows = data.get("balance", data) if isinstance(data, dict) else data
            if isinstance(rows, dict):
                rows = [rows]
            fallback: Decimal | None = None
            for item in rows or []:
                if not isinstance(item, dict):
                    continue
                asset = str(item.get("asset") or "").upper()
                if asset in ("USDT", "VST"):
                    return Decimal(str(item.get("equity") or item.get("balance") or "0"))
                if fallback is None:
                    try:
                        value = Decimal(str(item.get("equity") or item.get("balance") or "0"))
                    except Exception:
                        continue
                    if value > 0:
                        fallback = value
            if fallback is not None:
                return fallback
        except Exception as exc:
            logger.warning("balance_v3 failed: %s", type(exc).__name__)
        bals = await self.get_account_balance()
        usdt = bals.get("USDT") or bals.get("VST")
        return usdt.total if usdt else Decimal("0")

    async def set_leverage(self, symbol: str, leverage: int, side: str = "LONG") -> None:
        params = {
            "symbol": symbol,
            "side": side.upper(),
            "leverage": int(leverage),
            "timestamp": int(time.time() * 1000),
        }
        await self._request("POST", _TRADE["leverage"], params=params, signed=True)

    async def set_margin_isolated(self, symbol: str) -> None:
        params = {
            "symbol": symbol,
            "marginType": "ISOLATED",
            "timestamp": int(time.time() * 1000),
        }
        try:
            await self._request("POST", _TRADE["margin_type"], params=params, signed=True)
        except Exception as exc:
            # already isolated is ok
            logger.info("set_margin_isolated %s: %s", symbol, type(exc).__name__)

    async def close_position(
        self,
        symbol: str,
        quantity: Decimal | None = None,
        price: Decimal | None = None,
    ) -> bool:
        """Market close via reduce-only opposite side."""
        positions = await self.get_positions()
        pos = next((p for p in positions if p.symbol == symbol), None)
        if not pos or pos.quantity <= 0:
            return True
        qty = quantity if quantity is not None else pos.quantity
        side = "SELL" if pos.side == "long" else "BUY"
        ps = "LONG" if pos.side == "long" else "SHORT"
        await self.place_order(
            symbol=symbol,
            side=side,
            order_type="MARKET",
            quantity=qty,
            position_side=ps,
            reduce_only=True,
        )
        return True
