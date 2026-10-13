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

def append_live_trade(row: dict[str, Any], path: Path | None = None) -> None:
    """Append one exit/entry row to models/zeus_live_trades.jsonl."""
    p = path or Path("models/zeus_live_trades.jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    with p.open("a", encoding="utf-8") as f:
        f.write(_json.dumps(row, ensure_ascii=False, default=str) + "\n")


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


def _price_near(a: Decimal, b: Decimal, pct: float = 0.0005) -> bool:
    """True if |a-b|/ref <= pct (default 0.05%)."""
    if a is None or b is None:
        return False
    ref = abs(b) if b != 0 else abs(a)
    if ref == 0:
        return a == b
    return abs(a - b) / ref <= Decimal(str(pct))


async def _existing_protect(
    client: Any,
    symbol: str,
    *,
    kind: str,
    side: str,
    level: Decimal,
) -> Any | None:
    """Return open protect order if same kind+level already exists (±pct)."""
    del side  # reserved for future side match; level+kind is enough now
    try:
        orders = await client.get_open_orders(symbol)
    except Exception:
        return None
    for o in orders:
        ot = str(getattr(o, "order_type", "") or "").upper()
        is_stop = kind == "stop" and "STOP" in ot and "TAKE_PROFIT" not in ot
        is_tp = kind == "tp" and "TAKE_PROFIT" in ot
        if not (is_stop or is_tp):
            continue
        px = getattr(o, "stop_price", None) or getattr(o, "price", None)
        if px is None:
            continue
        try:
            px_d = Decimal(str(px))
        except Exception:
            continue
        if _price_near(px_d, level):
            return o
    return None



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
    entry_price: Decimal | None = None  # signal price for slip vs fill



def _aggregate_exit_fills(
    fills: list,
    *,
    side: str,
    entry_px: float | None,
) -> tuple[float | None, float, str, float | None]:
    """From fill list → (exit_avg, fees, reason, qty)."""
    if not fills:
        return None, 0.0, "", None
    close_side = "BUY" if side == "short" else "SELL"
    chosen: list[tuple[float, float, float, str]] = []
    for f in fills:
        try:
            px = float(f.get("price") or f.get("avgPrice") or f.get("fillPrice") or 0)
            q = float(
                f.get("qty")
                or f.get("quantity")
                or f.get("filledQty")
                or f.get("execQty")
                or 0
            )
            fee = float(f.get("commission") or f.get("fee") or f.get("commissionAmount") or 0)
        except (TypeError, ValueError):
            continue
        if px <= 0 or q <= 0:
            continue
        ot = str(f.get("type") or f.get("orderType") or f.get("origType") or "").upper()
        fside = str(f.get("side") or "").upper()
        if fside and fside not in (close_side, "BUY", "SELL"):
            continue
        reason = "выход"
        if "STOP" in ot and "TAKE_PROFIT" not in ot:
            reason = "стоп"
        elif "TAKE_PROFIT" in ot:
            reason = "тейк"
        elif "LIQUIDATION" in ot:
            reason = "ликвидация"
        chosen.append((px, q, abs(fee), reason))
    if not chosen:
        return None, 0.0, "", None
    typed = [c for c in chosen if c[3] in ("стоп", "тейк", "ликвидация")]
    use = typed if typed else chosen
    tot_q = sum(c[1] for c in use)
    if tot_q <= 0:
        return None, 0.0, "", None
    avg = sum(c[0] * c[1] for c in use) / tot_q
    fees = sum(c[2] for c in use)
    reason_qty: dict[str, float] = {}
    for _px, q, _fee, r in use:
        reason_qty[r] = reason_qty.get(r, 0.0) + q
    reason = max(reason_qty.items(), key=lambda kv: kv[1])[0]
    return avg, fees, reason, tot_q


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

    async def sync_closed_plans(
        self,
        *,
        positions_path: Path | None = None,
        send_tg: Any = None,
    ) -> dict[str, Any]:
        """Plan flat on exchange → fills → exact 🏁; TG dedup; heat; trades.

        No fills yet → keep plan, retry next tick (no +? spam).
        After 6h without fills → one soft rocket, drop plan.
        """
        import time as _time
        from pathlib import Path as _Path

        from astra_bot.decision.live_positions import load_positions, remove_plan
        from astra_bot.decision.live_tg_flags import try_claim
        from astra_bot.decision.zeus_heat import DEFAULT_HEAT_PATH, ZeusHeatGovernor

        path = positions_path or _Path("models/zeus_live_positions.json")
        plans = load_positions(path)
        if not plans:
            return {"closed": [], "cancelled": 0, "pending_fill": []}

        exchange_syms: set[str] = set()
        for pos in await self.client.get_positions():
            qty = getattr(pos, "quantity", None) or Decimal("0")
            if qty and qty > 0:
                exchange_syms.add(getattr(pos, "symbol", "") or "")

        closed: list[str] = []
        pending_fill: list[str] = []
        cancelled = 0
        now = _time.time()

        for sym, plan in list(plans.items()):
            if not sym or sym in exchange_syms:
                continue
            try:
                orders = await self.client.get_open_orders(sym)
            except Exception as exc:
                logger.warning("sync_closed: get_open_orders %s: %s", sym, type(exc).__name__)
                orders = []
            for o in orders:
                ot = str(getattr(o, "order_type", "") or "").upper()
                if "STOP" not in ot and "TAKE_PROFIT" not in ot:
                    continue
                oid = getattr(o, "id", None)
                if not oid:
                    continue
                try:
                    if await self.client.cancel_order(sym, str(oid)):
                        cancelled += 1
                except Exception as exc:
                    logger.warning(
                        "sync_closed: cancel %s %s: %s",
                        sym, oid, type(exc).__name__,
                    )

            side = str(plan.get("side") or "").lower()
            side_ru = "ЛОНГ" if side == "long" else "ШОРТ" if side == "short" else side.upper()
            base = (sym.split("-")[0] if "-" in sym else sym).replace("USDT", "")
            pending = bool(plan.get("pending_verify"))
            signal_id = str(plan.get("signal_id") or plan.get("ts") or sym)

            try:
                entry_px = float(plan["entry"]) if plan.get("entry") is not None else None
            except (TypeError, ValueError):
                entry_px = None
            try:
                qty_plan = float(plan["qty"]) if plan.get("qty") is not None else None
            except (TypeError, ValueError):
                qty_plan = None

            start_ms = None
            try:
                ts = float(plan.get("ts") or 0)
                if ts > 1e12:
                    ts /= 1000.0
                if ts > 0:
                    start_ms = int((ts - 60) * 1000)
            except (TypeError, ValueError):
                start_ms = None

            fills: list = []
            try:
                if hasattr(self.client, "get_all_fills"):
                    fills = await self.client.get_all_fills(sym, start_ms=start_ms)
            except Exception as exc:
                logger.warning("sync_closed fills %s: %s", sym, type(exc).__name__)

            exit_fill, fees, exit_reason, qty_closed = _aggregate_exit_fills(
                fills, side=side, entry_px=entry_px
            )

            pnl: float | None = None
            slip_exit: float | None = None
            if exit_fill is not None and entry_px is not None and entry_px > 0:
                q = float(qty_closed or qty_plan or 0)
                if q > 0:
                    if side == "long":
                        pnl = (exit_fill - entry_px) * q - float(fees or 0)
                    else:
                        pnl = (entry_px - exit_fill) * q - float(fees or 0)
                ref = plan.get("stop") if exit_reason == "стоп" else plan.get("tp")
                try:
                    if ref is not None and float(ref) > 0:
                        slip_exit = (exit_fill - float(ref)) / float(ref) * 100.0
                        if side == "short":
                            slip_exit = -slip_exit
                except (TypeError, ValueError):
                    pass

            age_sec = now - float(plan.get("ts") or now)
            have_pnl = pnl is not None

            if pending and not have_pnl:
                if try_claim(sym, "exit_pending_verify", signal_id):
                    msg = f"🏁 {base} {side_ru}: план снят (pending_verify)"
                    if send_tg is not None:
                        try:
                            await send_tg(msg)
                        except Exception:
                            pass
                remove_plan(sym, path)
                closed.append(sym)
                continue

            if not have_pnl:
                if age_sec < 6 * 3600:
                    pending_fill.append(sym)
                    logger.info("sync_closed: %s no fills — keep plan (retry)", sym)
                    continue
                if try_claim(sym, "exit", signal_id):
                    msg = f"🏁 {base} {side_ru}: закрыта +? (филлы недоступны)"
                    if send_tg is not None:
                        try:
                            await send_tg(msg)
                        except Exception as exc:
                            logger.warning("sync_closed TG: %s", type(exc).__name__)
                remove_plan(sym, path)
                closed.append(sym)
                try:
                    append_live_trade(
                        {
                            "event": "exit",
                            "symbol": sym,
                            "side": side,
                            "entry": entry_px,
                            "exit": None,
                            "exit_reason": "no_fills",
                            "pnl": None,
                            "signal_id": signal_id,
                            "ts": now,
                        }
                    )
                except Exception:
                    pass
                continue

            reason = exit_reason or "выход"
            sign = "+" if pnl >= 0 else "−"
            msg = f"🏁 {base} {side_ru}: закрыта {sign}{abs(pnl):.2f}$ ({reason})"
            if try_claim(sym, "exit", signal_id):
                if send_tg is not None:
                    try:
                        await send_tg(msg)
                    except Exception as exc:
                        logger.warning("sync_closed TG: %s", type(exc).__name__)
            else:
                logger.info("sync_closed: skip dup TG %s", sym)

            try:
                append_live_trade(
                    {
                        "event": "exit",
                        "symbol": sym,
                        "side": side,
                        "entry": entry_px,
                        "exit": exit_fill,
                        "exit_reason": reason,
                        "pnl": round(pnl, 4),
                        "fees": float(fees or 0),
                        "slip_exit_pct": slip_exit,
                        "qty": qty_closed or qty_plan,
                        "signal_id": signal_id,
                        "ts": now,
                    }
                )
            except Exception as tex:
                logger.warning("append_live_trade: %s", type(tex).__name__)

            try:
                heat = ZeusHeatGovernor(state_path=DEFAULT_HEAT_PATH)
                heat.record(float(pnl))
            except Exception as hexc:
                logger.warning("heat.record: %s", type(hexc).__name__)

            remove_plan(sym, path)
            closed.append(sym)
            logger.info(
                "sync_closed: %s pnl=%.4f exit=%s reason=%s",
                sym, pnl, exit_fill, reason,
            )

        logger.info(
            "sync_closed_plans: closed=%s pending_fill=%s cancelled=%d",
            closed, pending_fill, cancelled,
        )
        return {"closed": closed, "cancelled": cancelled, "pending_fill": pending_fill}

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
            plan = get_plan(sym, path)
            has_tp = any(
                "take_profit" in str(getattr(o, "order_type", "")).lower()
                for o in orders
            )
            # stop without our plan → unsupervised close below
            if has_stop and plan and plan.get("stop"):
                # Follow-up #4: stop alive but TP missing → place TP only
                tp = plan.get("tp")
                if tp is not None and not has_tp:
                    side = str(plan.get("side") or getattr(p, "side", "long")).lower()
                    stop_side = "SELL" if side == "long" else "BUY"
                    ps = "LONG" if side == "long" else "SHORT"
                    try:
                        q = Decimal(str(plan.get("qty") or qty))
                        await self.client.place_order(
                            symbol=sym,
                            side=stop_side,
                            order_type="TAKE_PROFIT_MARKET",
                            quantity=q,
                            stop_price=Decimal(str(tp)),
                            position_side=ps,
                            reduce_only=False,
                            close_position=True,
                            client_order_id=unique_client_order_id(sym, "r"),
                        )
                        restored.append(sym)
                        logger.info(
                            "restore_missing_stops: %s TP re-placed @ %s (stop was live)",
                            sym, tp,
                        )
                    except Exception as exc:
                        logger.warning(
                            "restore_missing_stops: %s TP-only failed: %s",
                            sym, type(exc).__name__,
                        )
                continue
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
                            reduce_only=False,
                            close_position=True,
                            client_order_id=unique_client_order_id(sym, "r"),
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
                    "restore_missing_stops: %s naked/no-plan qty=%s has_stop=%s — closing market",
                    sym, qty, has_stop,
                )
                try:
                    try:
                        await self.client.cancel_all_orders(sym)
                    except Exception as cexc:
                        logger.warning(
                            "restore_missing_stops: cancel_all %s: %s",
                            sym, type(cexc).__name__,
                        )
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
        """Entry MARKET first, then STOP_MARKET + optional TP (hedge-safe).

        Hedge mode: do NOT send reduceOnly on TP (BingX 109400). Use positionSide
        + closePosition on stop. Dedup: skip stop/tp if same level already open.
        Raises after entry if protect fails (caller must journal entry first).
        """
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
        entry_cid = unique_client_order_id(plan.symbol, "e")
        entry = await self.client.place_order(
            symbol=plan.symbol,
            side=buy_sell,
            order_type="MARKET",
            quantity=plan.quantity,
            position_side=ps,
            client_order_id=entry_cid,
        )
        fill_px = getattr(entry, "filled_price", None) or getattr(entry, "price", None)
        plan_px = plan.entry_price
        slip_pct: float | None = None
        if fill_px is not None and plan_px is not None and float(plan_px) > 0:
            raw = (float(fill_px) - float(plan_px)) / float(plan_px)
            if not side_long:
                raw = -raw  # short: lower fill is adverse
            slip_pct = raw * 100.0
            logger.info(
                "slip entry %s plan=%s fill=%s adverse_pct=%.4f",
                plan.symbol, plan_px, fill_px, slip_pct,
            )
        stop_side = "SELL" if side_long else "BUY"
        stop = None
        existing_s = await _existing_protect(
            self.client, plan.symbol, kind="stop", side=stop_side, level=plan.stop_price
        )
        if existing_s is not None:
            logger.info("open_with_protection: %s stop already open @ %s — skip", plan.symbol, plan.stop_price)
            stop = existing_s
        else:
            stop_cid = unique_client_order_id(plan.symbol, "s")
            stop = await self.client.place_order(
                symbol=plan.symbol,
                side=stop_side,
                order_type="STOP_MARKET",
                quantity=plan.quantity,
                stop_price=plan.stop_price,
                position_side=ps,
                close_position=True,
                client_order_id=stop_cid,
            )
        tp = None
        if plan.take_profit_price is not None:
            existing_t = await _existing_protect(
                self.client,
                plan.symbol,
                kind="tp",
                side=stop_side,
                level=plan.take_profit_price,
            )
            if existing_t is not None:
                logger.info(
                    "open_with_protection: %s tp already open @ %s — skip",
                    plan.symbol,
                    plan.take_profit_price,
                )
                tp = existing_t
            else:
                tp_cid = unique_client_order_id(plan.symbol, "t")
                # Same shape as STOP: quantity + closePosition (no reduceOnly in hedge)
                tp = await self.client.place_order(
                    symbol=plan.symbol,
                    side=stop_side,
                    order_type="TAKE_PROFIT_MARKET",
                    quantity=plan.quantity,
                    stop_price=plan.take_profit_price,
                    position_side=ps,
                    reduce_only=False,
                    close_position=True,
                    client_order_id=tp_cid,
                )
        return {
            "entry": entry,
            "stop": stop,
            "tp": tp,
            "equity": str(eq),
            "entry_client_order_id": entry_cid,
            "fill_price": str(fill_px) if fill_px is not None else None,
            "plan_price": str(plan_px) if plan_px is not None else None,
            "slip_pct": slip_pct,
        }

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
