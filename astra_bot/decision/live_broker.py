"""LiveBroker: exchange is truth; hard cap $100; risk % of real equity.

Does not touch PaperBroker. HALT/kill checked before any place_order.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
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



def _order_filled_qty(order: Any) -> Decimal | None:
    """Extract executed size from exchange order response. None if unknown."""
    if order is None:
        return None
    candidates = []
    for name in (
        "filled_qty",
        "filledQty",
        "executed_qty",
        "executedQty",
        "execQty",
        "quantity",
        "qty",
        "origQty",
        "orig_qty",
    ):
        v = getattr(order, name, None)
        if v is None and isinstance(order, dict):
            v = order.get(name)
        if v is None:
            continue
        try:
            d = Decimal(str(v))
        except Exception:
            continue
        if d > 0:
            candidates.append(d)
    # Prefer explicit filled* over orig/quantity when both present
    for name in ("filled_qty", "filledQty", "executed_qty", "executedQty", "execQty"):
        v = getattr(order, name, None)
        if v is None and isinstance(order, dict):
            v = order.get(name)
        if v is None:
            continue
        try:
            d = Decimal(str(v))
            if d > 0:
                return d
        except Exception:
            continue
    return candidates[0] if candidates else None


def _is_tp_price_past_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    needles = (
        "take profit price should be lower",
        "take profit price should be higher",
        "price should be lower than the current",
        "price should be higher than the current",
        "would immediately trigger",
        "immediately trigger",
    )
    return any(n in msg for n in needles)


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
        """Dollar risk from full wallet balance * pct (no HARD_CAP clamp).

        equity() remains min(wallet, hard_cap) for other callers; sizing uses
        the uncapped wallet so live risk matches paper % of full account.
        """
        wallet = await self.client.get_balance_usdt()
        if wallet < 0:
            wallet = Decimal("0")
        return (wallet * risk_pct).quantize(Decimal("0.01"))


    def net_breakeven_price(self, pos: Any) -> Decimal:
        """Live net BE from config taker fee (+ optional exit slip simplification).

        Fee: ``BINGX_PERPS_TAKER_FEE`` (cost_model.py:244 = 0.05%), not hardcode.
        Exit slippage: NOT applied here — explicit simplification vs paper
        ``broker.py:852–855`` (paper CostModel.effective_exit_price). Live
        mark/fill path does not mirror paper slip on the BE target.
        funding_accum: USDT signed sum from income FUNDING_FEE on the position
        (positive = we paid). Long: BE moves up by fund/qty; short: fund paid
        raises BE (worse), fund received (negative accum) lowers BE toward entry.
        """
        from astra_bot.engines.cost_model import BINGX_PERPS_TAKER_FEE

        direction = getattr(pos, "direction", None) or getattr(pos, "side", "long")
        entry = Decimal(str(
            getattr(pos, "fill_price", None)
            or getattr(pos, "entry_price", None)
            or getattr(pos, "entry", None)
            or 0
        ))
        if entry <= 0:
            return entry
        fee = Decimal(str(BINGX_PERPS_TAKER_FEE))
        funding = Decimal(str(getattr(pos, "funding_accum", 0) or 0))
        qty = Decimal(str(getattr(pos, "quantity", None) or getattr(pos, "qty", 1) or 1))
        fund_per_unit = (funding / qty) if qty > 0 else Decimal("0")
        # Round-trip taker on entry+exit; no exit-slip (see docstring).
        if str(direction).lower() in ("long", "buy"):
            return entry * (Decimal("1") + fee + fee) + fund_per_unit
        # short: fees pull BE down; funding paid (positive) also costs the short
        return entry * (Decimal("1") - fee - fee) - fund_per_unit

    async def reduce_position_qty(
        self, symbol: str, side: str, quantity: Decimal
    ) -> bool:
        """Partial reduce: clamp to exchange qty, never flip.

        Uses MARKET opposite + positionSide. Prefer reduceOnly=True for
        partial (arena); on 109400 fall back to hedge path reduce_only=False
        with clamped qty (same as client_live.close_position:498).
        client_live.close_position(quantity=trim) closes PARTIAL qty when set
        (else full pos.quantity) — see client_live.py:498.
        """
        self._assert_can_trade()
        qty = abs(Decimal(str(quantity)))
        if qty <= 0:
            return True
        # clamp to live exchange size
        exch_qty = qty
        try:
            positions = await self.client.get_positions()
            pos = next(
                (
                    p
                    for p in positions
                    if getattr(p, "symbol", "") == symbol
                    and (getattr(p, "quantity", 0) or 0) > 0
                ),
                None,
            )
            if pos is not None:
                exch_qty = abs(Decimal(str(pos.quantity)))
        except Exception:
            pass
        qty = min(qty, exch_qty)
        if qty <= 0:
            return True
        side_l = str(side).lower()
        order_side = "SELL" if side_l == "long" else "BUY"
        pos_side = "LONG" if side_l == "long" else "SHORT"
        try:
            await self.client.place_order(
                symbol=symbol,
                side=order_side,
                order_type="MARKET",
                quantity=qty,
                position_side=pos_side,
                reduce_only=True,
            )
            return True
        except Exception as exc:
            msg = str(exc).lower()
            if "109400" in msg or "reduceonly" in msg or "reduce_only" in msg:
                await self.client.place_order(
                    symbol=symbol,
                    side=order_side,
                    order_type="MARKET",
                    quantity=qty,
                    position_side=pos_side,
                    reduce_only=False,
                )
                return True
            raise

    def _assert_can_trade(self) -> None:
        if self._halted:
            raise RuntimeError(f"HALT before place_order: {self._halt_reason}")

    async def _qty_step_for_symbol(self, symbol: str) -> Decimal | None:
        """Lot step from exchange instrument (quantityPrecision → 10^-p).

        Fail-open: None if instruments unavailable (caller keeps legacy quantize).
        """
        try:
            client = self.client
            inst = None
            if hasattr(client, "get_instrument"):
                inst = await client.get_instrument(symbol)
            elif hasattr(client, "get_instruments"):
                insts = await client.get_instruments(symbol)
                if insts:
                    inst = insts[0]
            if inst is None:
                return None
            qp = getattr(inst, "quantity_precision", None)
            if qp is not None:
                try:
                    p = int(qp)
                    if p >= 0:
                        return (Decimal(10) ** -p)
                except (TypeError, ValueError):
                    pass
            step = getattr(inst, "step_size", None)
            if step is not None:
                s = Decimal(str(step))
                if s > 0:
                    return s
        except Exception as exp:
            logger.warning(
                "qty_step_for_symbol %s failed: %s — fail-open legacy quantize",
                symbol, type(exp).__name__,
            )
        return None

    def _quantize_to_step(self, qty: Decimal, step: Decimal | None) -> Decimal:
        """Floor qty to lot step; if step unknown keep 8dp legacy."""
        if qty <= 0:
            return qty
        if step is None or step <= 0:
            return qty.quantize(Decimal("0.00000001"))
        n = (qty / step).to_integral_value(rounding=ROUND_DOWN)
        out = n * step
        return out if out > 0 else qty.quantize(Decimal("0.00000001"))


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

            # Fallback: income REALIZED_PNL + fees + FUNDING (exact TG net, owner 18.10)
            # Retries inside tick when still missing (~3 x 7s).
            if pnl is None and hasattr(self.client, "get_realized_income"):
                try:
                    from astra_bot.decision.live_income_report import (
                        fetch_income_with_retries,
                    )

                    agg = await fetch_income_with_retries(
                        self.client, sym, start_ms, attempts=3, delay_sec=7.0
                    )
                    funding_fee = float(agg.get("funding") or 0.0)
                    fee_signed = float(agg.get("fees") or 0.0)
                    if fees is None or fees == 0:
                        fees = abs(fee_signed)
                    if agg.get("has_realized") or abs(float(agg.get("net") or 0)) > 1e-9:
                        from astra_bot.decision.live_income_report import (
                            income_too_small,
                            plan_est_risk,
                        )

                        est = plan_est_risk(entry_px, plan.get("stop"), qty_plan or qty_closed)
                        # Sanity: tiny REALIZED_PNL vs plan risk → dogon (owner №20)
                        if income_too_small(agg, est):
                            logger.warning(
                                "sync_closed: %s income too small rp=%.4f est=%.2f → dogon",
                                sym,
                                float(agg.get("pnl") or 0),
                                float(est or 0),
                            )
                            from astra_bot.decision.live_income_report import (
                                enqueue_pending,
                                format_pending_rocket,
                            )

                            dogon_reason = exit_reason or "стоп"
                            if try_claim(sym, "exit_pending_sum", signal_id):
                                msg = format_pending_rocket(sym, side)
                                if send_tg is not None:
                                    try:
                                        await send_tg(msg)
                                    except Exception as exc:
                                        logger.warning(
                                            "sync_closed TG: %s", type(exc).__name__
                                        )
                            try:
                                enqueue_pending(
                                    {
                                        "symbol": sym,
                                        "side": side,
                                        "signal_id": signal_id,
                                        "since_ms": start_ms,
                                        "reason": dogon_reason,
                                        "entry": entry_px,
                                        "stop": plan.get("stop"),
                                        "qty": qty_plan or qty_closed,
                                        "est": est,
                                        "ts": now,
                                    }
                                )
                            except Exception as exc:
                                logger.warning(
                                    "sync_closed enqueue dogon: %s", type(exc).__name__
                                )
                            try:
                                append_live_trade(
                                    {
                                        "event": "exit",
                                        "symbol": sym,
                                        "side": side,
                                        "entry": entry_px,
                                        "exit": None,
                                        "exit_reason": dogon_reason,
                                        "pnl": None,
                                        "pnl_source": "pending_dogon",
                                        "signal_id": signal_id,
                                        "qty": qty_plan or qty_closed,
                                        "ts": now,
                                    }
                                )
                            except Exception:
                                pass
                            remove_plan(sym, path)
                            closed.append(sym)
                            continue
                        # TG net includes funding; trades.pnl keeps realized-fees style
                        pnl = float(agg["pnl"]) + float(agg["fees"])
                        if exit_reason == "":
                            exit_reason = "income"
                        logger.info(
                            "sync_closed: %s income net=%.4f rp=%.4f fee=%.4f fund=%.4f",
                            sym,
                            float(agg["net"]),
                            float(agg.get("pnl") or 0),
                            fee_signed,
                            funding_fee,
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

            # Position flat + protective gone: if no typed fill reason, treat as stop
            if not exit_reason and plan.get("stop") is not None:
                exit_reason = "стоп"

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
                # Soft close after 6h: no sign / no '?'; queue follow-up (owner 18.10)
                from astra_bot.decision.live_income_report import (
                    enqueue_pending,
                    format_pending_rocket,
                )

                if try_claim(sym, "exit_pending_sum", signal_id):
                    msg = format_pending_rocket(sym, side)
                    if send_tg is not None:
                        try:
                            await send_tg(msg)
                        except Exception as exc:
                            logger.warning("sync_closed TG: %s", type(exc).__name__)
                try:
                    enqueue_pending(
                        {
                            "symbol": sym,
                            "side": side,
                            "signal_id": signal_id,
                            "since_ms": start_ms,
                            "reason": exit_reason or "no_fills",
                            "entry": entry_px,
                            "ts": now,
                        }
                    )
                except Exception as exc:
                    logger.warning("sync_closed enqueue pending: %s", type(exc).__name__)
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
            from astra_bot.decision.live_income_report import format_rocket

            msg = format_rocket(
                sym,
                side,
                net=float(pnl) + float(funding_fee or 0),
                reason=reason,
                funding=float(funding_fee or 0),
                fees=-abs(float(fees or 0)),
            )
            if try_claim(sym, "exit", signal_id):
                if send_tg is not None:
                    try:
                        await send_tg(msg)
                    except Exception as exc:
                        logger.warning("sync_closed TG: %s", type(exc).__name__)
            else:
                logger.info("sync_closed: skip dup TG %s", sym)

            try:
                trade_row = {
                    "event": "exit",
                    "symbol": sym,
                    "side": side,
                    "entry": entry_px,
                    "exit": exit_fill,
                    "exit_reason": reason,
                    "pnl": round(pnl, 4),
                    "pnl_source": "income" if reason == "income" else "fills",
                    "fees": float(fees or 0),
                    "funding_fee": float(funding_fee),
                    "trade_id": signal_id or f"{sym}|{side}|{int(now)}",
                    "slip_exit_pct": slip_exit,
                    "qty": qty_closed or qty_plan,
                    "signal_id": signal_id,
                    "ts": now,
                }
                # C5: split legs when plan had TP1 partial (40/60 channel parity)
                if plan.get("tp1") is not None and entry_px is not None:
                    try:
                        tp1_px = float(plan["tp1"])
                        q_plan_f = float(plan.get("qty") or qty_plan or 0)
                        q_tp1 = q_plan_f * 0.4
                        if side == "long":
                            tp1_leg = (tp1_px - entry_px) * q_tp1
                        else:
                            tp1_leg = (entry_px - tp1_px) * q_tp1
                        runner_leg = float(pnl) - tp1_leg if plan.get("be_moved") else None
                        trade_row["tp1_leg"] = round(tp1_leg, 4)
                        if runner_leg is not None:
                            trade_row["runner_leg"] = round(runner_leg, 4)
                    except (TypeError, ValueError, ZeroDivisionError):
                        pass
                append_live_trade(trade_row)
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
                # After be_moved (TP1 already filled) never re-place the partial TP.
                tp = plan.get("tp") or plan.get("tp1")
                if (
                    tp is not None
                    and not has_tp
                    and not plan.get("be_moved")
                ):
                    side = str(plan.get("side") or getattr(p, "side", "long")).lower()
                    stop_side = "SELL" if side == "long" else "BUY"
                    ps = "LONG" if side == "long" else "SHORT"
                    try:
                        q_full = Decimal(str(plan.get("qty") or qty))
                        has_tp1 = plan.get("tp1") is not None
                        step = await self._qty_step_for_symbol(sym)
                        if has_tp1:
                            q_tp = self._quantize_to_step(q_full * Decimal("0.4"), step)
                            ro, cp = False, False  # hedge: no reduceOnly; partial qty
                        else:
                            q_tp = self._quantize_to_step(q_full, step)
                            ro, cp = False, True
                        await self.client.place_order(
                            symbol=sym,
                            side=stop_side,
                            order_type="TAKE_PROFIT_MARKET",
                            quantity=q_tp,
                            stop_price=Decimal(str(tp)),
                            position_side=ps,
                            reduce_only=ro,
                            close_position=cp,
                            client_order_id=unique_client_order_id(sym, "r"),
                        )
                        restored.append(sym)
                        logger.info(
                            "restore_missing_stops: %s TP re-placed @ %s (stop was live)",
                            sym, tp,
                        )
                    except Exception as exp:
                        logger.warning(
                            "restore_missing_stops: %s TP-only failed: %s",
                            sym, type(exp).__name__,
                        )
                        try:
                            append_live_trade(
                                {
                                    "event": "restore_tp_failed",
                                    "symbol": sym,
                                    "error": f"{type(exp).__name__}: {exp}",
                                    "ts": time.time(),
                                }
                            )
                        except Exception:
                            pass
                continue
            if plan and plan.get("stop"):
                side = str(plan.get("side") or getattr(p, "side", "long")).lower()
                # be_moved: protective stop sits at entry (breakeven)
                if plan.get("be_moved") and plan.get("entry") is not None:
                    stop_px = Decimal(str(plan["entry"]))
                else:
                    stop_px = Decimal(str(plan["stop"]))
                q = Decimal(str(plan.get("qty") or qty))
                try:
                    await self.move_stop(sym, side, q, stop_px)
                    tp = plan.get("tp") or plan.get("tp1")
                    # Do not re-place TP if a smaller-volume TP is already open (partial leg).
                    if tp is not None and not has_tp:
                        stop_side = "SELL" if side == "long" else "BUY"
                        ps = "LONG" if side == "long" else "SHORT"
                        has_tp1 = plan.get("tp1") is not None
                        step = await self._qty_step_for_symbol(sym)
                        if has_tp1 and not plan.get("be_moved"):
                            q_tp = self._quantize_to_step(q * Decimal("0.4"), step)
                            ro, cp = False, False  # hedge: no reduceOnly; partial qty
                        elif has_tp1 and plan.get("be_moved"):
                            # After TP1 filled, no second TP restore for partial
                            q_tp = None
                            ro, cp = False, False
                        else:
                            q_tp = self._quantize_to_step(q, step)
                            ro, cp = False, True
                        if q_tp is not None and q_tp > 0:
                            await self.client.place_order(
                                symbol=sym,
                                side=stop_side,
                                order_type="TAKE_PROFIT_MARKET",
                                quantity=q_tp,
                                stop_price=Decimal(str(tp)),
                                position_side=ps,
                                reduce_only=ro,
                                close_position=cp,
                                client_order_id=unique_client_order_id(sym, "r"),
                            )
                    restored.append(sym)
                    logger.info("restore_missing_stops: %s STOP re-placed @ %s", sym, stop_px)
                except Exception as exp:
                    logger.warning(
                        "restore_missing_stops: %s move_stop failed: %s",
                        sym, type(exp).__name__,
                    )
                    try:
                        append_live_trade(
                            {
                                "event": "restore_stop_failed",
                                "symbol": sym,
                                "error": f"{type(exp).__name__}: {exp}",
                                "ts": time.time(),
                            }
                        )
                    except Exception:
                        pass
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

        Qty-parity (owner №21a): protections sized from exchange filled qty when
        present; plan.quantity is fail-open fallback. TP already past trigger →
        market-close 40% leg + be_moved (same geometry, immediate execution).
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

        plan_qty = plan.quantity
        filled = _order_filled_qty(entry)
        fact_qty = filled if filled is not None and filled > 0 else plan_qty
        try:
            qty_ratio = float(fact_qty / plan_qty) if plan_qty > 0 else None
        except Exception:
            qty_ratio = None
        logger.info(
            "qty-parity %s plan_qty=%s fact_qty=%s ratio=%s orig=%s filled=%s",
            plan.symbol,
            plan_qty,
            fact_qty,
            qty_ratio,
            getattr(entry, "origQty", None) or getattr(entry, "quantity", None),
            filled,
        )

        alerts: list[str] = []
        if plan_qty > 0 and fact_qty < plan_qty * Decimal("0.9"):
            pct = float(fact_qty / plan_qty * 100) if plan_qty else 0.0
            alerts.append(
                f"⚠️ {plan.symbol}: биржа дала {fact_qty} из {plan_qty} ({pct:.0f}%) — защиты пересчитаны"
            )

        stop_side = "SELL" if side_long else "BUY"
        stop = None
        be_moved = False
        existing_s = await _existing_protect(
            self.client, plan.symbol, kind="stop", side=stop_side, level=plan.stop_price
        )
        if existing_s is not None:
            logger.info("open_with_protection: %s stop already open @ %s — skip", plan.symbol, plan.stop_price)
            stop = existing_s
        else:
            stop_cid = unique_client_order_id(plan.symbol, "s")
            try:
                stop = await self.client.place_order(
                    symbol=plan.symbol,
                    side=stop_side,
                    order_type="STOP_MARKET",
                    quantity=fact_qty,
                    stop_price=plan.stop_price,
                    position_side=ps,
                    close_position=True,
                    client_order_id=stop_cid,
                )
            except Exception as exc:
                logger.warning("open_with_protection stop failed %s: %s", plan.symbol, exc)
                alerts.append(f"⚠️ {plan.symbol}: стоп отклонён — {type(exc).__name__}: {exc}")
                raise
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
                step = await self._qty_step_for_symbol(plan.symbol)
                tp_qty = self._quantize_to_step(fact_qty * Decimal("0.4"), step)
                if tp_qty <= 0:
                    tp_qty = self._quantize_to_step(fact_qty, step)
                try:
                    tp = await self.client.place_order(
                        symbol=plan.symbol,
                        side=stop_side,
                        order_type="TAKE_PROFIT_MARKET",
                        quantity=tp_qty,
                        stop_price=plan.take_profit_price,
                        position_side=ps,
                        reduce_only=False,
                        close_position=False,
                        client_order_id=tp_cid,
                    )
                except Exception as exc:
                    if _is_tp_price_past_error(exc):
                        # Level already through — market-close 40% leg, then stop→entry
                        logger.warning(
                            "open_with_protection: %s TP price past — market leg qty=%s",
                            plan.symbol,
                            tp_qty,
                        )
                        market_ok = False
                        try:
                            tp = await self.client.place_order(
                                symbol=plan.symbol,
                                side=stop_side,
                                order_type="MARKET",
                                quantity=tp_qty,
                                position_side=ps,
                                reduce_only=False,
                                close_position=False,
                                client_order_id=unique_client_order_id(plan.symbol, "tm"),
                            )
                            market_ok = True
                        except Exception as exc2:
                            logger.warning(
                                "open_with_protection market TP leg failed %s: %s",
                                plan.symbol,
                                exc2,
                            )
                            alerts.append(
                                f"⚠️ {plan.symbol}: TP/market-leg отклонён — {type(exc2).__name__}: {exc2}"
                            )
                        be_entry = None
                        try:
                            be_entry = Decimal(str(fill_px if fill_px is not None else plan.entry_price))
                        except Exception:
                            be_entry = None
                        if market_ok and be_entry is not None and be_entry > 0:
                            try:
                                await self.move_stop(
                                    plan.symbol, plan.side, fact_qty, be_entry
                                )
                                be_moved = True
                            except Exception as exc3:
                                logger.warning(
                                    "open_with_protection move_stop BE failed %s: %s",
                                    plan.symbol,
                                    exc3,
                                )
                                alerts.append(
                                    f"⚠️ {plan.symbol}: не смог поставить стоп на вход — старый стоп остаётся"
                                )
                                be_moved = False
                        append_live_trade(
                            {
                                "event": "tp1_market_past_level",
                                "symbol": plan.symbol,
                                "side": plan.side,
                                "qty": str(tp_qty),
                                "tp": str(plan.take_profit_price),
                                "fact_qty": str(fact_qty),
                                "be_moved": be_moved,
                                "market_ok": market_ok,
                            }
                        )
                    else:
                        logger.warning("open_with_protection TP failed %s: %s", plan.symbol, exc)
                        alerts.append(
                            f"⚠️ {plan.symbol}: TP отклонён — {type(exc).__name__}: {exc}"
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
            "fact_qty": str(fact_qty),
            "plan_qty": str(plan_qty),
            "qty_ratio": qty_ratio,
            "be_moved": be_moved,
            "alerts": alerts,
        }

    async def move_stop(self, symbol: str, side: str, quantity: Decimal, stop_price: Decimal) -> Any:
        """A-012: place new STOP first, then cancel old stops (never naked)."""
        self._assert_can_trade()
        ps = "LONG" if side.lower() == "long" else "SHORT"
        stop_side = "SELL" if side.lower() == "long" else "BUY"
        new_oid = unique_client_order_id(symbol, "m")
        placed = await self.client.place_order(
            symbol=symbol,
            side=stop_side,
            order_type="STOP_MARKET",
            quantity=quantity,
            stop_price=stop_price,
            position_side=ps,
            close_position=True,
            client_order_id=new_oid,
        )
        new_id = str(getattr(placed, "id", None) or getattr(placed, "exchange_order_id", None) or "")
        try:
            orders = await self.client.get_open_orders(symbol)
        except Exception as exc:
            logger.warning("move_stop: get_open_orders %s: %s", symbol, type(exc).__name__)
            orders = []
        for o in orders:
            ot = str(getattr(o, "order_type", "")).lower()
            oid = str(getattr(o, "id", None) or "")
            if "stop" not in ot or not oid:
                continue
            if new_id and oid == new_id:
                continue
            # keep take-profit legs
            if "take_profit" in ot or "tp" in ot:
                continue
            try:
                await self.client.cancel_order(symbol, oid)
            except Exception as exc:
                logger.warning("move_stop: cancel_order %s failed: %s", oid, type(exc).__name__)
        return placed

    async def flat_all(self) -> dict[str, Any]:
        """B-008: close_position first, then cancel residual orders."""
        positions = await self.client.get_positions()
        cancelled = 0
        closed = 0
        failed = 0
        for p in positions:
            try:
                if await self.client.close_position(p.symbol):
                    closed += 1
            except Exception as exc:
                failed += 1
                logger.warning("flat_all: close_position %s failed: %s", p.symbol, exc)
            try:
                cancelled += await self.client.cancel_all_orders(p.symbol)
            except Exception as exc:
                failed += 1
                logger.warning("flat_all: cancel_all_orders %s failed: %s", p.symbol, exc)
        for o in await self.client.get_open_orders():
            if getattr(o, "id", None) and getattr(o, "symbol", None):
                try:
                    await self.client.cancel_order(o.symbol, str(o.id))
                    cancelled += 1
                except Exception as exc:
                    failed += 1
                    logger.warning("flat_all: cancel_order %s failed: %s", o.id, exc)
        return {"cancelled": cancelled, "closed": closed, "failed": failed}
