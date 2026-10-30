"""LiveBroker: exchange is truth; hard cap $100; risk % of real equity.

Does not touch PaperBroker. HALT/kill checked before any place_order.
"""
from __future__ import annotations

import logging
import re
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


def _journal_protect(event: str, **fields) -> None:
    """Owner 20.10 §2: every protect attempt — success and failure — to trades journal."""
    try:
        row = {"event": event, "ts": time.time(), **fields}
        append_live_trade(row)
    except Exception:
        pass
    try:
        from astra_bot.decision.live_arm_wiring import append_journal
        append_journal({"event": event, "ts": time.time(), **fields})
    except Exception:
        pass
    if event == "protect_place_failed":
        try:
            from astra_bot.decision.live_margin_warn import maybe_warn_margin_reject
            maybe_warn_margin_reject(
                str(fields.get("symbol") or ""),
                str(fields.get("error") or ""),
                None,  # uses clock-installed callback if any
            )
        except Exception:
            pass




def journal_order_cancel(
    *,
    symbol: str,
    order_id: str | None,
    order_type: str,
    reason: str,
    path: str,
    side: str | None = None,
    extra: dict | None = None,
) -> None:
    """F15b §6: every cancel path writes order_cancel to journal."""
    try:
        from astra_bot.decision.live_arm_wiring import append_journal
        row = {
            "event": "order_cancel",
            "symbol": symbol,
            "order_id": str(order_id or ""),
            "type": order_type,
            "reason": reason,
            "path": path,
            "side": side,
            "ts": time.time(),
        }
        if extra:
            row.update(extra)
        append_journal(row)
    except Exception:
        pass
    try:
        append_live_trade({
            "event": "order_cancel",
            "symbol": symbol,
            "order_id": str(order_id or ""),
            "type": order_type,
            "reason": reason,
            "path": path,
            "ts": time.time(),
        })
    except Exception:
        pass


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



def _tp_frac_from_plan(plan) -> Decimal:
    """F15: leg share from plan.tp_frac / tp_frac_1, else 0.3."""
    if plan is None:
        return Decimal("0.3")
    if isinstance(plan, dict):
        v = plan.get("tp_frac", plan.get("tp_frac_1"))
    else:
        v = getattr(plan, "tp_frac", None)
    try:
        d = Decimal(str(v if v is not None else "0.3"))
    except Exception:
        d = Decimal("0.3")
    if d <= 0 or d >= 1:
        return Decimal("0.3")
    return d

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
    tp_frac: float = 0.3  # F15: PLAN_TP_FRACTIONS[0]; fallback 0.3
    initial_stop: Decimal | None = None  # F15b: stop at entry; clamp TP from this only



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

        F24c: protect path — allowed under kill-halt (entries stay blocked).
        """
        qty = abs(Decimal(str(quantity)))
        if qty <= 0:
            return True
        # clamp to live exchange size — fail-closed if positions unknown
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
            if pos is None:
                return False
            exch_qty = abs(Decimal(str(pos.quantity)))
        except Exception:
            return False
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


    async def reconcile_plan_qty(
        self,
        *,
        positions_path: Path | None = None,
        threshold: float = 0.005,
    ) -> dict[str, Any]:
        """F13: |exch_qty-plan.qty|/plan.qty > 0.5% → upsert qty + journal qty_reconcile."""
        from astra_bot.decision.live_arm_wiring import append_journal
        from astra_bot.decision.live_positions import load_positions, plan_key, save_positions

        path = positions_path or Path("models/zeus_live_positions.json")
        out: dict[str, Any] = {"reconciled": [], "checked": 0}
        try:
            positions = await self.client.get_positions()
        except Exception as exc:
            logger.warning("reconcile_plan_qty get_positions: %s", type(exc).__name__)
            return {**out, "error": type(exc).__name__}
        exch: dict[str, float] = {}
        for pos in positions or []:
            sym = getattr(pos, "symbol", None)
            side = str(getattr(pos, "side", "") or "").lower()
            if not sym or side not in ("long", "short"):
                continue
            try:
                q = abs(float(getattr(pos, "quantity", 0) or 0))
            except (TypeError, ValueError):
                continue
            if q <= 0:
                continue
            exch[plan_key(str(sym), side)] = q
        plans = load_positions(path)
        changed = False
        for key, plan in list(plans.items()):
            if not isinstance(plan, dict):
                continue
            out["checked"] += 1
            try:
                plan_qty = abs(float(plan.get("qty") or 0))
            except (TypeError, ValueError):
                continue
            if plan_qty <= 0:
                continue
            exch_qty = exch.get(key)
            if exch_qty is None:
                continue
            rel = abs(exch_qty - plan_qty) / plan_qty
            if rel <= threshold:
                continue
            old_qty = plan_qty
            plan["qty"] = float(exch_qty)
            if plan.get("plan_qty") not in (None, ""):
                try:
                    pq = float(plan["plan_qty"])
                    if pq:
                        plan["qty_ratio"] = exch_qty / pq
                except (TypeError, ValueError):
                    pass
            plans[key] = plan
            changed = True
            # F16b: JSON-safe numbers (Decimal from exchange must not break journal)
            row = {
                "event": "qty_reconcile",
                "symbol": plan.get("symbol") or key.split("|")[0],
                "side": plan.get("side") or (key.split("|")[1] if "|" in key else None),
                "old_qty": float(old_qty),
                "new_qty": float(exch_qty),
                "rel_diff": float(round(rel, 6)),
                "signal_id": plan.get("signal_id"),
                "ts": time.time(),
            }
            plan["qty"] = float(exch_qty)
            out["reconciled"].append(row)
            # F16: journal MUST land — silent pass was losing qty_reconcile while
            # plan.qty already moved (LTC 474.7→199.4 without journal row).
            try:
                append_journal(row)
            except Exception as exc:
                logger.warning(
                    "qty_reconcile journal failed %s: %s",
                    row.get("symbol"),
                    type(exc).__name__,
                )
                out.setdefault("journal_errors", []).append(
                    {"symbol": row.get("symbol"), "error": type(exc).__name__}
                )
            try:
                append_live_trade(row)
            except Exception as exc:
                logger.debug(
                    "qty_reconcile trade-log skip %s: %s",
                    row.get("symbol"),
                    type(exc).__name__,
                )
        if changed:
            try:
                save_positions(plans, path)
            except Exception as exc:
                logger.warning("reconcile_plan_qty save: %s", type(exc).__name__)
                out["save_error"] = type(exc).__name__
        return out

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
                from astra_bot.decision.live_income_report import strategy_from_signal_id
                # Owner 28.10: classify income exits + R observability
                exchange_fill_type = "UNKNOWN"
                if reason in ("стоп", "stop", "stop_loss"):
                    exchange_fill_type = "ENGINE_STOP"
                elif reason in ("тейк", "tp", "take_profit") or (reason and "тейк" in str(reason)):
                    exchange_fill_type = "ENGINE_TP"
                elif reason == "income":
                    exchange_fill_type = "UNKNOWN"
                r_multiple = None
                try:
                    risk_px = plan.get("initial_stop") or plan.get("stop")
                    risk = None
                    if entry_px is not None and risk_px is not None:
                        risk = abs(float(entry_px) - float(risk_px))
                    if risk and risk > 0 and exit_fill is not None:
                        if side == "long":
                            r_multiple = (float(exit_fill) - float(entry_px)) / risk
                        else:
                            r_multiple = (float(entry_px) - float(exit_fill)) / risk
                    elif risk and risk > 0 and pnl is not None and qty_closed:
                        # Owner 28.10: fill price often missing on VST income path —
                        # approximate R from pnl / (risk_px * qty)
                        try:
                            risk_usd = risk * abs(float(qty_closed))
                            if risk_usd > 0:
                                r_multiple = float(pnl) / risk_usd
                        except Exception:
                            pass
                except Exception:
                    r_multiple = None
                scratched = False
                try:
                    if r_multiple is not None and abs(float(r_multiple)) < 0.1:
                        scratched = True
                except Exception:
                    pass
                # anomaly: income close far from plan stop/tp
                if reason == "income":
                    try:
                        from astra_bot.decision.live_arm_wiring import append_journal as _aj
                        stop_p = float(plan["stop"]) if plan.get("stop") is not None else None
                        tp_p = float(plan["tp"]) if plan.get("tp") is not None else None
                        xf = float(exit_fill) if exit_fill is not None else None
                        near_stop = stop_p is not None and xf is not None and abs(xf - stop_p) / max(abs(stop_p), 1e-12) < 0.003
                        near_tp = tp_p is not None and xf is not None and abs(xf - tp_p) / max(abs(tp_p), 1e-12) < 0.003
                        if not near_stop and not near_tp:
                            exchange_fill_type = "UNKNOWN"
                            _aj({
                                "event": "exit_income_anomaly",
                                "symbol": sym,
                                "side": side,
                                "exit_reason": reason,
                                "fill_price": xf,
                                "plan_stop": stop_p,
                                "plan_tp": tp_p,
                                "mark_price_at_exit": None,
                                "current_exchange_stop": stop_p,
                                "r_multiple": r_multiple,
                                "pnl": pnl,
                                "ts": now,
                            })
                        elif near_stop:
                            exchange_fill_type = "ENGINE_STOP"
                        elif near_tp:
                            exchange_fill_type = "ENGINE_TP"
                    except Exception:
                        pass
                # Telemetry: exit_price alias, initial_stop, mfe_r from plan peak if any
                init_stop_v = plan.get("initial_stop") or plan.get("stop")
                try:
                    init_stop_f = float(init_stop_v) if init_stop_v is not None else None
                except (TypeError, ValueError):
                    init_stop_f = None
                mfe_r_v = None
                try:
                    if plan.get("mfe_r") is not None:
                        mfe_r_v = round(float(plan["mfe_r"]), 4)
                    elif plan.get("mfe") is not None and entry_px is not None and init_stop_f is not None:
                        risk_abs = abs(float(entry_px) - float(init_stop_f))
                        if risk_abs > 0:
                            mfe_px = float(plan["mfe"])
                            if side == "long":
                                mfe_r_v = round((mfe_px - float(entry_px)) / risk_abs, 4)
                            else:
                                mfe_r_v = round((float(entry_px) - mfe_px) / risk_abs, 4)
                except (TypeError, ValueError, ZeroDivisionError):
                    mfe_r_v = None
                trade_row = {
                    "event": "exit",
                    "symbol": sym,
                    "side": side,
                    "entry": entry_px,
                    "exit": exit_fill,
                    "exit_price": exit_fill,  # explicit alias for reports
                    "exit_reason": reason,
                    "pnl": round(pnl, 4),
                    "pnl_source": "income" if reason == "income" else "fills",
                    "fees": float(fees or 0),
                    "funding_fee": float(funding_fee),
                    "trade_id": signal_id or f"{sym}|{side}|{int(now)}",
                    "slip_exit_pct": slip_exit,
                    "qty": qty_closed or qty_plan,
                    "signal_id": signal_id,
                    "strategy": strategy_from_signal_id(signal_id),
                    "ts": now,
                    "r_multiple": None if r_multiple is None else round(float(r_multiple), 4),
                    "exchange_fill_type": exchange_fill_type,
                    "scratched": scratched,
                    "initial_stop": init_stop_f,
                    "mfe_r": mfe_r_v,
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
        send_tg=None,
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
                            q_tp = self._quantize_to_step(q_full * _tp_frac_from_plan(plan), step)
                            ro, cp = False, False  # F4: hedge forbids ReduceOnly (109400)
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
                        err_s = f"{type(exp).__name__}: {exp}"
                        try:
                            append_live_trade(
                                {
                                    "event": "restore_tp_failed",
                                    "symbol": sym,
                                    "error": err_s,
                                    "ts": time.time(),
                                }
                            )
                        except Exception:
                            pass
                        try:
                            from astra_bot.decision.live_arm_wiring import append_journal
                            append_journal(
                                {
                                    "event": "restore_tp_failed",
                                    "symbol": sym,
                                    "error": err_s,
                                    "ts": time.time(),
                                }
                            )
                        except Exception:
                            pass
                        try:
                            from astra_bot.decision.live_margin_warn import maybe_warn_margin_reject
                            maybe_warn_margin_reject(sym, err_s, send_tg)
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
                            q_tp = self._quantize_to_step(q * _tp_frac_from_plan(plan), step)
                            ro, cp = False, False  # F4: hedge forbids ReduceOnly (109400)
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
                        try:
                            _oo = await self.client.get_open_orders(sym)
                        except Exception:
                            _oo = []
                        for _o in _oo or []:
                            _oid = getattr(_o, "id", None) or getattr(_o, "order_id", None)
                            _ot = str(getattr(_o, "type", "") or getattr(_o, "order_type", "") or "")
                            _ou = _ot.upper()
                            _otype = (
                                "STOP" if "STOP" in _ou else
                                ("TP" if ("TAKE" in _ou or "PROFIT" in _ou) else (_ot or "UNKNOWN"))
                            )
                            journal_order_cancel(
                                symbol=sym,
                                order_id=str(_oid) if _oid is not None else None,
                                order_type=_otype,
                                reason="restore_naked_close",
                                path="restore_missing_stops.cancel_all",
                                side=None,
                            )
                        await self.client.cancel_all_orders(sym)
                    except Exception as cexc:
                        logger.warning(
                            "restore_missing_stops: cancel_all %s: %s",
                            sym, type(cexc).__name__,
                        )
                    await self.client.close_position(sym, quantity=None)
                    remove_plan(sym, path)
                    closed.append(sym)
                except Exception as exc:
                    logger.warning(
                        "restore_missing_stops: close %s failed: %s",
                        sym, type(exc).__name__,
                    )
        return {"restored": restored, "closed_naked": closed}


    async def ensure_protection(
        self,
        *,
        positions_path: Path | None = None,
        send_tg=None,
        trigger_be_r: float | None = None,
        bare_state_path: Path | None = None,
    ) -> dict[str, Any]:
        """Owner 20.10 §4 + arena B2: protect or close ONE symbol — never global flat.

        trigger_be_r: default ZEUS_BE_TRIGGER_R or 0.5 (parity with clock BE gate).

        Safety:
        - get_open_orders failure → abort pass (None sentinel), close nothing
        - place ok but verify get_open_orders fails → UNKNOWN, close nothing
        - close only after confirmed exchange rejection AND two consecutive bare ticks
        - market-close first, then cancel leftover orders (not the reverse)
        - emergency close uses client.close_position (positionSide path, no upside-down qty)
        """
        import os as _os
        from pathlib import Path as _Path

        from astra_bot.decision.live_positions import get_plan, remove_plan, upsert_plan

        if trigger_be_r is None:
            try:
                trigger_be_r = float((_os.environ.get("ZEUS_BE_TRIGGER_R") or "0.5").strip() or "0.5")
            except Exception:
                trigger_be_r = 0.5
            if trigger_be_r <= 0 or trigger_be_r > 3:
                trigger_be_r = 0.5

        path = positions_path or _Path("models/zeus_live_positions.json")
        state_path = bare_state_path or _Path("models/zeus_protect_bare_ticks.json")

        def _load_bare() -> dict:
            try:
                if state_path.exists():
                    import json as _json
                    return _json.loads(state_path.read_text(encoding="utf-8") or "{}")
            except Exception:
                pass
            return {}

        def _save_bare(st: dict) -> None:
            try:
                import json as _json
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(_json.dumps(st, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

        def _is_confirmed_reject(exc: BaseException) -> bool:
            """Business reject only — not transport/5xx (arena residual a)."""
            msg = str(exc)
            m = re.search(r"place_order rejected code=(\S+)", msg, re.I)
            if m:
                code = m.group(1).strip().strip("'\"")
                try:
                    ci = int(float(code))
                except ValueError:
                    return code not in ("0", "None", "null", "")
                if ci == 0 or 500 <= ci <= 599:
                    return False
                return True
            low = msg.lower()
            if "109400" in low or "order size must be less" in low:
                return True
            if "insufficient margin" in low or "insufficient available" in low:
                return True
            return False

        positions = await self.client.get_positions()
        open_orders: list | None
        try:
            open_orders = await self.client.get_open_orders()
        except Exception as exc:
            logger.warning(
                "ensure_protection: get_open_orders FAILED — abort pass, close nothing: %s",
                type(exc).__name__,
            )
            if send_tg is not None:
                try:
                    await send_tg("⚠️ ensure_protection: get_open_orders failed — pass aborted")
                except Exception:
                    pass
            return {"placed": [], "closed": [], "aborted": "get_open_orders"}

        by_sym: dict[str, list] = {}
        for o in open_orders or []:
            by_sym.setdefault(getattr(o, "symbol", "") or "", []).append(o)

        bare = _load_bare()
        placed: list[str] = []
        closed: list[str] = []
        still_bare: set[str] = set()

        for p in positions:
            sym = getattr(p, "symbol", "") or ""
            qty = Decimal(str(getattr(p, "quantity", 0) or 0))
            if not sym or qty <= 0:
                continue
            orders = by_sym.get(sym, [])
            has_stop = any(
                "stop" in str(getattr(o, "order_type", "")).lower()
                and "take_profit" not in str(getattr(o, "order_type", "")).lower()
                for o in orders
            )
            if has_stop:
                bare.pop(sym, None)
                continue

            plan = get_plan(sym, path) or {}
            side = str(plan.get("side") or getattr(p, "side", "long") or "long").lower()
            entry = plan.get("entry") or getattr(p, "entry_price", None) or getattr(p, "avg_price", None)
            stop_px = plan.get("stop")
            if plan.get("be_moved") and entry is not None:
                stop_px = entry
            mark = (
                getattr(p, "mark_price", None)
                or getattr(p, "markPrice", None)
                or getattr(p, "price", None)
            )

            placed_ok = False
            verify_unknown = False
            confirmed_reject = False

            if stop_px is not None:
                try:
                    await self.move_stop(sym, side, qty, Decimal(str(stop_px)))
                    try:
                        oo = await self.client.get_open_orders(sym)
                        placed_ok = any(
                            "stop" in str(getattr(o, "order_type", "")).lower()
                            and "take_profit" not in str(getattr(o, "order_type", "")).lower()
                            for o in (oo or [])
                        )
                    except Exception as vexc:
                        # B2b: place may have succeeded — do NOT close
                        verify_unknown = True
                        logger.warning(
                            "ensure_protection: verify get_open_orders %s failed after place: %s",
                            sym, type(vexc).__name__,
                        )
                        _journal_protect(
                            "protect_place",
                            symbol=sym,
                            kind="stop_ensure",
                            stop_price=str(stop_px),
                            verified=False,
                            verify_unknown=True,
                        )
                    if placed_ok:
                        placed.append(sym)
                        bare.pop(sym, None)
                        _journal_protect(
                            "protect_place",
                            symbol=sym,
                            kind="stop_ensure",
                            stop_price=str(stop_px),
                            qty=str(qty),
                            verified=True,
                        )
                except Exception as exc:
                    confirmed_reject = _is_confirmed_reject(exc)
                    _journal_protect(
                        "protect_place_failed",
                        symbol=sym,
                        kind="stop_ensure",
                        stop_price=str(stop_px),
                        error=f"{type(exc).__name__}: {exc}",
                        confirmed_reject=confirmed_reject,
                    )

            if placed_ok or verify_unknown:
                continue

            # BE fallback only on confirmed reject + mfe
            mfe_r = 0.0
            try:
                if entry is not None and plan.get("stop") is not None and mark is not None:
                    e = Decimal(str(entry))
                    s = Decimal(str(plan.get("stop")))
                    m = Decimal(str(mark))
                    r_abs = abs(e - s)
                    if r_abs > 0:
                        mfe_r = float((m - e) / r_abs) if side == "long" else float((e - m) / r_abs)
            except Exception:
                mfe_r = 0.0

            if confirmed_reject and mfe_r >= float(trigger_be_r) and entry is not None:
                try:
                    await self.move_stop(sym, side, qty, Decimal(str(entry)))
                    try:
                        oo = await self.client.get_open_orders(sym)
                        if any(
                            "stop" in str(getattr(o, "order_type", "")).lower()
                            and "take_profit" not in str(getattr(o, "order_type", "")).lower()
                            for o in (oo or [])
                        ):
                            placed.append(sym)
                            bare.pop(sym, None)
                            try:
                                upsert_plan(
                                    symbol=sym, side=side,
                                    entry=Decimal(str(entry)),
                                    stop=Decimal(str(entry)),
                                    qty=qty, path=path,
                                    tp=plan.get("tp"), tp1=plan.get("tp1"),
                                    be_moved=True, signal_id=plan.get("signal_id"),
                                )
                            except Exception:
                                pass
                            _journal_protect(
                                "protect_place",
                                symbol=sym,
                                kind="stop_be_fallback",
                                mfe_r=round(mfe_r, 4),
                                verified=True,
                            )
                            continue
                    except Exception:
                        # verify unknown after BE place — leave alone
                        continue
                except Exception as exc:
                    confirmed_reject = confirmed_reject and _is_confirmed_reject(exc)
                    _journal_protect(
                        "protect_place_failed",
                        symbol=sym,
                        kind="stop_be_fallback",
                        error=f"{type(exc).__name__}: {exc}",
                    )

            # B2d: two consecutive ticks of confirmed bare before close
            if not confirmed_reject:
                # network / unknown — do not increment toward close
                still_bare.add(sym)
                continue

            n = int(bare.get(sym, 0) or 0) + 1
            bare[sym] = n
            still_bare.add(sym)
            if n < 2:
                if n == 1 and send_tg is not None:
                    try:
                        await send_tg(f"⚠️ {sym}: защитный ордер отклонён биржей — повтор (1/2)")
                    except Exception:
                        pass
                logger.warning(
                    "ensure_protection: %s bare tick %s/2 (confirmed reject) — wait",
                    sym, n,
                )
                continue

            # B2c/e: close FIRST, then cancel remnants
            try:
                await self.client.close_position(sym, quantity=None)
                # confirm flat
                try:
                    pos2 = await self.client.get_positions()
                    still = any(
                        (getattr(x, "symbol", "") == sym)
                        and Decimal(str(getattr(x, "quantity", 0) or 0)) > 0
                        for x in (pos2 or [])
                    )
                except Exception:
                    still = True  # unknown — do not cancel stops under open pos
                if not still:
                    try:
                        await self.client.cancel_all_orders(sym)
                    except Exception:
                        pass
                    try:
                        remove_plan(sym, path)
                    except Exception:
                        pass
                    bare.pop(sym, None)
                    closed.append(sym)
                    msg = f"⚠️ {sym}: нет стопа 2 тика — закрыта по рынку (protect_failed_closed)"
                    _journal_protect(
                        "protect_failed_closed",
                        symbol=sym,
                        side=side,
                        qty=str(qty),
                        bare_ticks=n,
                    )
                    if send_tg is not None:
                        try:
                            await send_tg(msg)
                        except Exception:
                            pass
                else:
                    logger.warning(
                        "ensure_protection: %s close called but position still open — keep orders",
                        sym,
                    )
                    _journal_protect(
                        "protect_failed_closed",
                        symbol=sym,
                        error="close_not_confirmed_still_open",
                    )
            except Exception as exc:
                logger.warning("ensure_protection close %s: %s", sym, type(exc).__name__)
                _journal_protect(
                    "protect_failed_closed",
                    symbol=sym,
                    error=f"{type(exc).__name__}: {exc}",
                )
                if n == 2 and send_tg is not None:
                    try:
                        await send_tg(
                            f"⚠️ {sym}: нет стопа 2 тика, рыночное закрытие НЕ удалось — нужно ручное вмешательство"
                        )
                    except Exception:
                        pass

        # drop symbols no longer bare / open
        for k in list(bare.keys()):
            if k not in still_bare and k not in closed:
                # protected this tick or not in positions
                if k not in {getattr(p, "symbol", "") for p in positions}:
                    bare.pop(k, None)
        _save_bare(bare)
        return {"placed": placed, "closed": closed, "bare": bare}


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
                # STOP: closePosition=true closes remainder; qty still required by BingX API
                stop = await self.client.place_order(
                    symbol=plan.symbol,
                    side=stop_side,
                    order_type="STOP_MARKET",
                    quantity=fact_qty,
                    stop_price=plan.stop_price,
                    position_side=ps,
                    close_position=True,
                    reduce_only=False,
                    client_order_id=stop_cid,
                )
                _journal_protect(
                    "protect_place",
                    symbol=plan.symbol,
                    kind="stop",
                    stop_price=str(plan.stop_price),
                    qty=str(fact_qty),
                    close_position=True,
                    order_id=str(getattr(stop, "id", None) or ""),
                )
            except Exception as exc:
                logger.warning("open_with_protection stop failed %s: %s", plan.symbol, exc)
                alerts.append(f"⚠️ {plan.symbol}: стоп отклонён — {type(exc).__name__}: {exc}")
                _journal_protect(
                    "protect_place_failed",
                    symbol=plan.symbol,
                    kind="stop",
                    stop_price=str(plan.stop_price),
                    qty=str(fact_qty),
                    error=f"{type(exc).__name__}: {exc}",
                )
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
                tp_qty = self._quantize_to_step(fact_qty * _tp_frac_from_plan(plan), step)
                if tp_qty <= 0:
                    tp_qty = self._quantize_to_step(fact_qty, step)
                try:
                    # F4/owner 22.10: hedge forbids ReduceOnly field (BingX 109400)
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
                    _journal_protect(
                        "protect_place",
                        symbol=plan.symbol,
                        kind="tp",
                        stop_price=str(plan.take_profit_price),
                        qty=str(tp_qty),
                        reduce_only=False,
                        order_id=str(getattr(tp, "id", None) or ""),
                    )
                except Exception as exc:
                    _journal_protect(
                        "protect_place_failed",
                        symbol=plan.symbol,
                        kind="tp",
                        stop_price=str(plan.take_profit_price),
                        qty=str(tp_qty),
                        error=f"{type(exc).__name__}: {exc}",
                        reduce_only=False,
                    )
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
        """A-012: place new STOP first, then cancel old stops (never naked).

        F12: on hedge reject «Position SL order already exists» cancel existing
        STOP_* for this symbol/side by id, then one place retry. No amend API in
        client_live (grep) — cancel-first is the only path; naked window covered
        by restore_missing_stops next tick.

        F24c: protect path — allowed under kill-halt (entries stay blocked).
        """
        ps = "LONG" if side.lower() == "long" else "SHORT"
        stop_side = "SELL" if side.lower() == "long" else "BUY"

        def _is_stop_order(o: Any) -> bool:
            ot = str(getattr(o, "order_type", "") or getattr(o, "type", "") or "").lower()
            if "take_profit" in ot or ot.startswith("tp"):
                return False
            return "stop" in ot

        def _oids_from(orders: list) -> list[str]:
            out: list[str] = []
            for o in orders:
                if not _is_stop_order(o):
                    continue
                oid = str(getattr(o, "id", None) or getattr(o, "exchange_order_id", None) or "")
                if oid:
                    out.append(oid)
            return out

        async def _open_stops() -> list:
            try:
                return list(await self.client.get_open_orders(symbol) or [])
            except Exception as exc:
                logger.warning("move_stop: get_open_orders %s: %s", symbol, type(exc).__name__)
                return []

        async def _place() -> Any:
            new_oid = unique_client_order_id(symbol, "m")
            return await self.client.place_order(
                symbol=symbol,
                side=stop_side,
                order_type="STOP_MARKET",
                quantity=quantity,
                stop_price=stop_price,
                position_side=ps,
                close_position=True,
                client_order_id=new_oid,
            )

        def _already_exists(exc: BaseException) -> bool:
            msg = str(exc).lower()
            return (
                "already exists" in msg
                or "sl order already" in msg
                or "101406" in msg
                or "109406" in msg
            )

        def _lower_price(exc: BaseException) -> bool:
            msg = str(exc).lower()
            return "should be lower than the current price" in msg or "should be greater" in msg

        before = await _open_stops()
        before_ids = _oids_from(before)
        placed = None
        cancel_retry = False
        reject_reason: str | None = None
        try:
            placed = await _place()
        except Exception as exc:
            if _already_exists(exc):
                reject_reason = "already_exists"
                cancel_retry = True
                cancelled: list[str] = []
                for o in before:
                    if not _is_stop_order(o):
                        continue
                    # same position side when available
                    ops = str(getattr(o, "position_side", None) or getattr(o, "positionSide", None) or "").upper()
                    if ops and ops not in (ps, ""):
                        continue
                    oid = str(getattr(o, "id", None) or "")
                    if not oid:
                        continue
                    try:
                        if await self.client.cancel_order(symbol, oid):
                            cancelled.append(oid)
                            journal_order_cancel(
                                symbol=symbol,
                                order_id=oid,
                                order_type="STOP",
                                reason="already_exists_retry",
                                path="move_stop",
                                side=side,
                            )
                    except Exception as cexc:
                        logger.warning("move_stop cancel-retry %s: %s", oid, type(cexc).__name__)
                try:
                    from astra_bot.decision.live_arm_wiring import append_journal
                    append_journal({
                        "event": "move_stop_cancel_retry",
                        "symbol": symbol,
                        "side": side,
                        "cancelled_ids": cancelled,
                        "stop_order_ids_before": before_ids,
                        "stop_price": str(stop_price),
                        "reason": "already_exists",
                    })
                except Exception:
                    pass
                try:
                    placed = await _place()
                except Exception as retry_exc:
                    reject_reason = (
                        "lower_price" if _lower_price(retry_exc) else "already_exists_retry_failed"
                    )
                    try:
                        from astra_bot.decision.live_arm_wiring import append_journal
                        append_journal({
                            "event": "move_stop_reject",
                            "symbol": symbol,
                            "side": side,
                            "reason": reject_reason,
                            "stop_order_ids_before": before_ids,
                            "error": str(retry_exc)[:200],
                        })
                    except Exception:
                        pass
                    raise
            else:
                reject_reason = "lower_price" if _lower_price(exc) else "place_failed"
                try:
                    from astra_bot.decision.live_arm_wiring import append_journal
                    append_journal({
                        "event": "move_stop_reject",
                        "symbol": symbol,
                        "side": side,
                        "reason": reject_reason,
                        "stop_order_ids_before": before_ids,
                        "error": str(exc)[:200],
                    })
                except Exception:
                    pass
                raise

        new_id = str(getattr(placed, "id", None) or getattr(placed, "exchange_order_id", None) or "")
        orders = await _open_stops()
        after_ids = _oids_from(orders)
        for o in orders:
            if not _is_stop_order(o):
                continue
            oid = str(getattr(o, "id", None) or "")
            if not oid or (new_id and oid == new_id):
                continue
            try:
                await self.client.cancel_order(symbol, oid)
                journal_order_cancel(
                    symbol=symbol,
                    order_id=oid,
                    order_type="STOP",
                    reason="replace_after_place",
                    path="move_stop",
                    side=side,
                )
            except Exception as exc:
                logger.warning("move_stop: cancel_order %s failed: %s", oid, type(exc).__name__)
        try:
            from astra_bot.decision.live_arm_wiring import append_journal
            append_journal({
                "event": "move_stop_done",
                "symbol": symbol,
                "side": side,
                "stop_price": str(stop_price),
                "new_id": new_id,
                "stop_order_ids_before": before_ids,
                "stop_order_ids_after": after_ids,
                "cancel_retry": cancel_retry,
            })
        except Exception:
            pass
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
