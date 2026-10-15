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
    """From fill list → (exit_avg, fees_all, reason, qty).

    Exit avg/qty: only close-side fills (strict). Fees: ALL fills in window
    (open + close) so opening commission is not dropped.
    """
    if not fills:
        return None, 0.0, "", None
    close_side = "BUY" if side == "short" else "SELL"
    chosen: list[tuple[float, float, float, str]] = []
    fees_all = 0.0
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
            fee = abs(float(f.get("commission") or f.get("fee") or f.get("commissionAmount") or 0))
        except (TypeError, ValueError):
            continue
        # P3.2: every fill in the plan window contributes to fees
        if fee > 0:
            fees_all += fee
        if px <= 0 or q <= 0:
            continue
        fside = str(f.get("side") or "").upper()
        # P2.1: strict — only closing side enters exit avg
        if fside and fside != close_side:
            continue
        ot = str(f.get("type") or f.get("orderType") or f.get("origType") or "").upper()
        reason = "выход"
        if "STOP" in ot and "TAKE_PROFIT" not in ot:
            reason = "стоп"
        elif "TAKE_PROFIT" in ot:
            reason = "тейк"
        elif "LIQUIDATION" in ot:
            reason = "ликвидация"
        chosen.append((px, q, fee, reason))
    if not chosen:
        return None, fees_all, "", None
    typed = [c for c in chosen if c[3] in ("стоп", "тейк", "ликвидация")]
    use = typed if typed else chosen
    tot_q = sum(c[1] for c in use)
    if tot_q <= 0:
        return None, fees_all, "", None
    avg = sum(c[0] * c[1] for c in use) / tot_q
    reason_qty: dict[str, float] = {}
    for _px, q, _fee, r in use:
        reason_qty[r] = reason_qty.get(r, 0.0) + q
    reason = max(reason_qty.items(), key=lambda kv: kv[1])[0]
    return avg, fees_all, reason, tot_q


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

        for _key, plan in list(plans.items()):
            sym = str(plan.get("symbol") or _key.split("|")[0] or "")
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

            if start_ms is not None and fills:
                filtered = []
                for f in fills:
                    try:
                        fts = int(f.get("time") or f.get("ts") or f.get("T") or 0)
                    except (TypeError, ValueError):
                        fts = 0
                    if fts and fts < start_ms:
                        continue
                    filtered.append(f)
                fills = filtered
            exit_fill, fees, exit_reason, qty_closed = _aggregate_exit_fills(
                fills, side=side, entry_px=entry_px
            )

            # Prefer realizedPnl sum from fill rows when exchange provides it
            pnl: float | None = None
            slip_exit: float | None = None
            funding_fee: float = 0.0
            rpnl_sum = 0.0
            rpnl_n = 0
            for f in fills:
                try:
                    if f.get("realizedPnl") is not None and str(f.get("realizedPnl")) != "":
                        _rp = float(f["realizedPnl"])
                        if abs(_rp) > 1e-12:  # ignore 0.0 on open/non-closing fills
                            rpnl_sum += _rp
                            rpnl_n += 1
                    # fee field on allFillOrders is often negative = paid
                    if f.get("fee") is not None and not fees:
                        fees = abs(float(f.get("fee") or 0))
                except (TypeError, ValueError):
                    pass
            if rpnl_n:
                # realizedPnl on BingX fills is usually already net of fee for that leg
                pnl = rpnl_sum

            if pnl is None and exit_fill is not None and entry_px is not None and entry_px > 0:
                q = float(qty_closed or qty_plan or 0)
                if q > 0:
                    if side == "long":
                        pnl = (exit_fill - entry_px) * q - float(fees or 0)
                    else:
                        pnl = (entry_px - exit_fill) * q - float(fees or 0)

            # Fallback: user/income REALIZED_PNL + TRADING_FEE
            if pnl is None and hasattr(self.client, "get_realized_income"):
                try:
                    inc = await self.client.get_realized_income(sym, start_ms=start_ms)
                    rp = 0.0
                    fee_i = 0.0
                    fp = 0.0
                    for row in inc:
                        try:
                            val = float(row.get("income") or 0)
                        except (TypeError, ValueError):
                            continue
                        it = str(row.get("_incomeType") or row.get("incomeType") or "")
                        if it == "REALIZED_PNL":
                            rp += val
                        elif it == "TRADING_FEE":
                            fee_i += abs(val)
                        elif it == "FUNDING_FEE":
                            fp += abs(val)
                    if fp:
                        funding_fee = float(fp)
                    if rp != 0.0 or fee_i != 0.0:
                        # A-011 Q3: PnL as paper — without funding; funding separate field
                        pnl = (rp - fee_i) if rp != 0.0 else None
                        if fees is None or fees == 0:
                            fees = fee_i
                        if exit_reason == "":
                            exit_reason = "income"
                        logger.info(
                            "sync_closed: %s pnl from income rp=%.4f fee=%.4f fund=%.4f (fund excluded from pnl)",
                            sym, rp, fee_i, fp,
                        )
                except Exception as exc:
                    logger.warning("sync_closed income %s: %s", sym, type(exc).__name__)

            # slip vs plan stop/tp (all exit branches — not only income)
            if exit_fill is not None:
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
            if try_claim(sym, "exit", signal_id):
                pnl_s = f"{pnl:+.2f}$" if pnl is not None else "+?"
                exit_s = f"{exit_fill:.6g}" if exit_fill is not None else "?"
                msg = f"🏁 {base} {side_ru}: {reason} {pnl_s} exit={exit_s}"
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
                        "pnl": pnl,
                        "fees": float(fees or 0),
                        "funding_fee": float(funding_fee),
                        "trade_id": signal_id or f"{sym}|{side}|{int(now)}",
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

            # VOL_EXPANSION anti-chatter parity (owner 15.10)
            # until = closed_ts + 1800; idempotent by trade_id (sync re-sees exit)
            try:
                from astra_bot.decision.reentry_guard import (
                    is_vol_expansion_reason,
                )
                from astra_bot.decision.reentry_guard import (
                    set_cooldown as _vol_set_cd,
                )

                if is_vol_expansion_reason(reason):
                    _side = str(plan.get("side") or "").lower()
                    if _side in ("long", "short"):
                        _vol_set_cd(
                            sym,
                            _side,
                            trade_id=str(plan.get("signal_id") or "") or None,
                            exit_reason=str(reason),
                            closed_ts=float(now) if now else None,
                        )
            except Exception as vexc:
                logger.debug("vol_cooldown set: %s", type(vexc).__name__)

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
        from astra_bot.decision.live_positions import load_positions

        path = positions_path or Path("models/zeus_live_positions.json")
        plans = load_positions(path)
        restored = 0
        closed_naked = 0
        for pos in await self.client.get_positions():
            qty = getattr(pos, "quantity", None) or Decimal("0")
            if not qty or qty <= 0:
                continue
            sym = getattr(pos, "symbol", "") or ""
            if not sym:
                continue
            orders = await self.client.get_open_orders(sym)
            has_stop = any(
                "STOP" in str(getattr(o, "order_type", "") or "").upper()
                and "TAKE_PROFIT" not in str(getattr(o, "order_type", "") or "").upper()
                for o in orders
            )
            if has_stop:
                continue
            plan = plans.get(sym)
            if plan and plan.get("stop") is not None:
                try:
                    stop_px = Decimal(str(plan["stop"]))
                    tp_px = Decimal(str(plan["tp"])) if plan.get("tp") is not None else None
                    side = str(plan.get("side") or "long")
                    await self.move_stop(sym, stop_px, take_profit=tp_px, side=side)
                    restored += 1
                    logger.warning("restore_stop: %s stop=%s from plan", sym, stop_px)
                except Exception as exc:
                    logger.error("restore_stop %s failed: %s", sym, type(exc).__name__)
            else:
                logger.error("NAKED position %s qty=%s — no plan stop, market close", sym, qty)
                try:
                    await self.client.close_position(sym, quantity=qty)
                    closed_naked += 1
                except Exception as exc:
                    logger.error("naked close %s failed: %s", sym, type(exc).__name__)
        return {"restored": restored, "closed_naked": closed_naked}

    async def open_with_protection(self, plan: LiveEntryPlan) -> dict[str, Any]:
        """Entry + STOP_MARKET (+ optional TP). Exchange is truth after fill."""
        self._assert_can_trade()
        if not self._entries_allowed:
            raise RuntimeError("entries blocked")
        side = plan.side.lower()
        if side not in ("long", "short"):
            raise ValueError(f"side must be long|short, got {side}")
        lev = min(int(plan.leverage or self.config.default_leverage), self.config.max_leverage)
        try:
            await self.client.set_margin_isolated(plan.symbol)
        except Exception as exc:
            logger.warning("set_margin_isolated %s: %s", plan.symbol, type(exc).__name__)
        try:
            await self.client.set_leverage(plan.symbol, lev, side="LONG" if side == "long" else "SHORT")
        except Exception as exc:
            logger.warning("set_leverage %s: %s", plan.symbol, type(exc).__name__)

        order_side = "BUY" if side == "long" else "SELL"
        cid = unique_client_order_id(plan.symbol, "e")
        entry = await self.client.place_order(
            symbol=plan.symbol,
            side=order_side,
            order_type="MARKET",
            quantity=plan.quantity,
            client_order_id=cid,
            reduce_only=False,
        )
        fill_price = getattr(entry, "avg_price", None) or getattr(entry, "price", None)
        slip_pct = None
        if fill_price is not None and plan.entry_price is not None and plan.entry_price > 0:
            try:
                fp = float(fill_price)
                ep = float(plan.entry_price)
                slip_pct = (fp - ep) / ep * 100.0
                if side == "short":
                    slip_pct = -slip_pct
            except Exception:
                pass

        # Protective STOP
        stop_side = "SELL" if side == "long" else "BUY"
        existing = await _existing_protect(
            self.client, plan.symbol, kind="stop", side=stop_side, level=plan.stop_price
        )
        if existing is None:
            scid = unique_client_order_id(plan.symbol, "s")
            await self.client.place_order(
                symbol=plan.symbol,
                side=stop_side,
                order_type="STOP_MARKET",
                quantity=plan.quantity,
                stop_price=plan.stop_price,
                client_order_id=scid,
                reduce_only=True,
            )

        if plan.take_profit_price is not None:
            tp_side = stop_side
            existing_tp = await _existing_protect(
                self.client, plan.symbol, kind="tp", side=tp_side, level=plan.take_profit_price
            )
            if existing_tp is None:
                tcid = unique_client_order_id(plan.symbol, "t")
                try:
                    await self.client.place_order(
                        symbol=plan.symbol,
                        side=tp_side,
                        order_type="TAKE_PROFIT_MARKET",
                        quantity=plan.quantity,
                        stop_price=plan.take_profit_price,
                        client_order_id=tcid,
                        reduce_only=True,
                    )
                except Exception as exc:
                    logger.warning("TP place %s: %s", plan.symbol, type(exc).__name__)

        return {
            "symbol": plan.symbol,
            "side": side,
            "quantity": str(plan.quantity),
            "fill_price": str(fill_price) if fill_price is not None else None,
            "slip_pct": slip_pct,
            "stop": str(plan.stop_price),
            "tp": str(plan.take_profit_price) if plan.take_profit_price else None,
            "leverage": lev,
            "entry_order": entry,
        }

    async def move_stop(
        self,
        symbol: str,
        stop_price: Decimal,
        *,
        take_profit: Decimal | None = None,
        side: str = "long",
        quantity: Decimal | None = None,
    ) -> dict[str, Any]:
        """Cancel existing STOP/TP and place new ones (reduceOnly)."""
        self._assert_can_trade()
        side = side.lower()
        stop_side = "SELL" if side == "long" else "BUY"
        try:
            orders = await self.client.get_open_orders(symbol)
        except Exception:
            orders = []
        for o in orders:
            ot = str(getattr(o, "order_type", "") or "").upper()
            if "STOP" in ot or "TAKE_PROFIT" in ot:
                oid = getattr(o, "id", None)
                if oid:
                    try:
                        await self.client.cancel_order(symbol, str(oid))
                    except Exception as exc:
                        logger.warning("move_stop cancel %s: %s", oid, type(exc).__name__)
        qty = quantity
        if qty is None:
            for p in await self.client.get_positions():
                if getattr(p, "symbol", "") == symbol:
                    q = getattr(p, "quantity", None) or Decimal("0")
                    if q > 0:
                        qty = q
                        break
        if qty is None or qty <= 0:
            raise RuntimeError(f"move_stop: no qty for {symbol}")
        scid = unique_client_order_id(symbol, "s")
        await self.client.place_order(
            symbol=symbol,
            side=stop_side,
            order_type="STOP_MARKET",
            quantity=qty,
            stop_price=stop_price,
            client_order_id=scid,
            reduce_only=True,
        )
        if take_profit is not None:
            tcid = unique_client_order_id(symbol, "t")
            try:
                await self.client.place_order(
                    symbol=symbol,
                    side=stop_side,
                    order_type="TAKE_PROFIT_MARKET",
                    quantity=qty,
                    stop_price=take_profit,
                    client_order_id=tcid,
                    reduce_only=True,
                )
            except Exception as exc:
                logger.warning("move_stop TP %s: %s", symbol, type(exc).__name__)
        return {"symbol": symbol, "stop": str(stop_price), "tp": str(take_profit) if take_profit else None}

    async def flat_all(self) -> dict[str, Any]:
        """Market close all positions + cancel protect orders."""
        self._assert_can_trade()
        closed = 0
        cancelled = 0
        failed = 0
        for p in await self.client.get_positions():
            qty = getattr(p, "quantity", None) or Decimal("0")
            sym = getattr(p, "symbol", "") or ""
            if qty and qty > 0 and sym:
                try:
                    await self.client.close_position(sym, quantity=qty)
                    closed += 1
                except Exception as exc:
                    failed += 1
                    logger.warning("flat_all: close %s failed: %s", sym, type(exc).__name__)
        for o in await self.client.get_open_orders():
            oid = getattr(o, "id", None)
            sym = getattr(o, "symbol", "") or ""
            if oid and sym:
                try:
                    await self.client.cancel_order(sym, str(oid))
                    cancelled += 1
                except Exception as exc:
                    failed += 1
                    logger.warning("flat_all: cancel_order %s failed: %s", oid, exc)
        return {"cancelled": cancelled, "closed": closed, "failed": failed}
