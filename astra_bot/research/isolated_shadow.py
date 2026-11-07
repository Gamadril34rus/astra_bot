"""Pure research-only signal filtering, event replay and virtual accounting.

This module intentionally has no exchange, broker, live-wiring or project-state imports.
Execution is recorded only when a replay input supplies an observable execution price.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
import hashlib
import math
from typing import Any


class ZoneState(StrEnum):
    OUTSIDE_NEAR = "OUTSIDE_NEAR"
    OUTSIDE_FAR = "OUTSIDE_FAR"
    IN_ZONE = "IN_ZONE"
    EMPTY_ZONES = "EMPTY_ZONES"
    NO_OPPOSING_ZONE = "NO_OPPOSING_ZONE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ZoneDecision:
    state: ZoneState
    boundary: float | None = None
    distance_pct: float | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Signal:
    signal_id: str
    symbol: str
    side: str
    strategy: str
    formed_at_ms: int
    market_received_at_ms: int
    zone_received_at_ms: int
    source_closed_bar_ms: int | None
    decision_at_ms: int
    predecision_price: float | None
    price_source: str
    entry_reference: float
    stop_reference: float
    target_reference: float | None
    confidence: float | None = None
    timeframe: str = "4h"
    source: str = "zeus_live_arm_feed"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VariantDecision:
    variant: str
    accepted_by_variant: bool
    reason: str
    zone: ZoneDecision


def classify_zone(
    *,
    side: str,
    price: float | None,
    zones: list[dict[str, float]] | None,
    market_received_at_ms: int | None,
    zone_received_at_ms: int | None,
    decision_at_ms: int,
    max_age_ms: int = 30_000,
    threshold_pct: float = 1.0,
) -> ZoneDecision:
    if (
        side not in {"long", "short"}
        or price is None
        or not math.isfinite(price)
        or price <= 0
    ):
        return ZoneDecision(ZoneState.UNKNOWN, reason="invalid_side_or_price")
    if market_received_at_ms is None or zone_received_at_ms is None:
        return ZoneDecision(ZoneState.UNKNOWN, reason="missing_receive_timestamp")
    ages = (
        decision_at_ms - market_received_at_ms,
        decision_at_ms - zone_received_at_ms,
    )
    if min(ages) < 0:
        return ZoneDecision(ZoneState.UNKNOWN, reason="future_dated_snapshot")
    if max(ages) > max_age_ms:
        return ZoneDecision(ZoneState.UNKNOWN, reason="stale_snapshot")
    if zones is None:
        return ZoneDecision(ZoneState.UNKNOWN, reason="zones_unavailable")

    valid: list[tuple[float, float]] = []
    for zone in zones:
        try:
            lower = float(zone["lower"])
            upper = float(zone["upper"])
            if math.isfinite(lower) and math.isfinite(upper) and 0 < lower <= upper:
                valid.append((lower, upper))
        except (KeyError, TypeError, ValueError):
            continue
    if not valid:
        return ZoneDecision(ZoneState.EMPTY_ZONES, reason="no_valid_zones")
    if any(lower <= price <= upper for lower, upper in valid):
        return ZoneDecision(ZoneState.IN_ZONE, reason="price_inside_zone")

    if side == "long":
        opposing = [(lower, upper) for lower, upper in valid if lower > price]
        if not opposing:
            return ZoneDecision(ZoneState.NO_OPPOSING_ZONE, reason="no_zone_above_price")
        boundary = min(lower for lower, _ in opposing)
    else:
        opposing = [(lower, upper) for lower, upper in valid if upper < price]
        if not opposing:
            return ZoneDecision(ZoneState.NO_OPPOSING_ZONE, reason="no_zone_below_price")
        boundary = max(upper for _, upper in opposing)

    distance = 100.0 * abs(boundary - price) / price
    state = (
        ZoneState.OUTSIDE_NEAR
        if distance <= threshold_pct
        else ZoneState.OUTSIDE_FAR
    )
    return ZoneDecision(state, boundary, distance, "boundary_distance")


def select_baseline_signal(
    channel: dict[str, Any] | None,
    multi_4h: dict[str, Any] | None,
    multi_1h: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Single shared source event: channel first, then 4h and 1h multi fallback."""
    return channel or multi_4h or multi_1h


def variant_decision(
    variant: str, signal: Signal, zone: ZoneDecision
) -> VariantDecision:
    if variant not in {"A", "B", "C"}:
        raise ValueError(f"unknown variant {variant}")
    if variant == "B":
        if signal.symbol not in {"BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT"}:
            return VariantDecision(variant, False, "B_UNIVERSE_EXCLUDED", zone)
        if signal.strategy == "zeus_channel_boundary" and signal.side != "short":
            return VariantDecision(variant, False, "B_CHANNEL_SHORT_ONLY", zone)
        return VariantDecision(variant, True, "B_RULES_PASS", zone)
    if variant == "C" and zone.state == ZoneState.OUTSIDE_NEAR:
        return VariantDecision(variant, False, "C_OPPOSING_ZONE_WITHIN_1PCT", zone)
    return VariantDecision(variant, True, "BASE_RULES_PASS", zone)


@dataclass
class VirtualPosition:
    position_id: str
    order_id: str
    attempt_id: str
    signal_id: str
    symbol: str
    side: str
    strategy: str
    opened_at_ms: int
    entry_price: float
    stop_price: float
    target_price: float | None
    quantity: float
    initial_risk_cash: float
    entry_fee: float
    remaining_qty: float
    realized_gross: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    status: str = "OPEN"
    exit_reason: str | None = None

    @property
    def net_pnl(self) -> float:
        return self.realized_gross - self.fees + self.funding


@dataclass
class VirtualPortfolio:
    name: str
    initial_capital: float = 100_000.0
    risk_fraction: float = 0.01
    max_open: int = 5
    max_exposure_fraction: float = 0.30
    fee_taker: float = 0.0005
    slippage_bps: float = 5.0
    max_leverage: float = 6.0
    equity_cash: float = field(init=False)
    positions: dict[str, VirtualPosition] = field(default_factory=dict)
    closed: list[VirtualPosition] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    fills: list[dict[str, Any]] = field(default_factory=list)
    peak_equity: float = field(init=False)
    max_drawdown_pct: float = 0.0
    _seen_events: set[str] = field(default_factory=set)

    def __post_init__(self):
        self.equity_cash = float(self.initial_capital)
        self.peak_equity = self.equity_cash

    def equity(self, marks: dict[str, float] | None = None) -> float:
        marks = marks or {}
        unrealized = sum(
            (marks.get(position.symbol, position.entry_price) - position.entry_price)
            * position.remaining_qty
            * (1 if position.side == "long" else -1)
            for position in self.positions.values()
        )
        return self.equity_cash + unrealized

    def _mark(self, marks: dict[str, float] | None = None):
        equity = self.equity(marks)
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity > 0:
            drawdown = (self.peak_equity - equity) / self.peak_equity * 100
            self.max_drawdown_pct = max(self.max_drawdown_pct, drawdown)

    def attempt(
        self,
        signal: Signal,
        decision: VariantDecision,
        *,
        execution_price: float | None = None,
        attempt_id: str | None = None,
    ) -> dict[str, Any]:
        aid = attempt_id or hashlib.sha256(
            f"{self.name}|{signal.signal_id}|{len(self.attempts)}".encode()
        ).hexdigest()[:32]
        row = {
            "attempt_id": aid,
            "signal_id": signal.signal_id,
            "variant": self.name,
            "accepted_by_variant": decision.accepted_by_variant,
            "reason": decision.reason,
            "zone_state": decision.zone.state.value,
            "decision_at_ms": signal.decision_at_ms,
        }
        if not decision.accepted_by_variant:
            self.attempts.append(row)
            return row
        if len(self.positions) >= self.max_open:
            row.update(accepted_by_variant=False, reason="MAX_OPEN")
            self.attempts.append(row)
            return row
        if any(p.symbol == signal.symbol for p in self.positions.values()):
            row.update(accepted_by_variant=False, reason="SYMBOL_ALREADY_OPEN")
            self.attempts.append(row)
            return row
        price = execution_price
        if price is None or not math.isfinite(price) or price <= 0:
            row.update(accepted_by_variant=False, reason="UNKNOWN_EXECUTION_PRICE")
            self.attempts.append(row)
            return row
        slip = self.slippage_bps / 10_000
        fill_price = price * (1 + slip if signal.side == "long" else 1 - slip)
        risk_distance = abs(fill_price - signal.stop_reference)
        invalid_stop = (
            signal.side == "long" and signal.stop_reference >= fill_price
        ) or (
            signal.side == "short" and signal.stop_reference <= fill_price
        )
        invalid_target = (
            signal.target_reference is not None
            and (
                (signal.side == "long" and signal.target_reference <= fill_price)
                or (signal.side == "short" and signal.target_reference >= fill_price)
            )
        )
        if risk_distance <= 0 or invalid_stop or invalid_target:
            row.update(accepted_by_variant=False, reason="INVALID_INITIAL_RISK")
            self.attempts.append(row)
            return row
        equity = self.equity()
        risk_cash = equity * self.risk_fraction
        quantity = risk_cash / risk_distance
        gross_exposure = sum(
            position.remaining_qty * position.entry_price
            for position in self.positions.values()
        )
        exposure_room = max(
            0.0, equity * self.max_exposure_fraction - gross_exposure
        )
        leverage_room = max(0.0, equity * self.max_leverage - gross_exposure)
        quantity = min(
            quantity,
            exposure_room / fill_price,
            leverage_room / fill_price,
        )
        if quantity <= 0:
            row.update(accepted_by_variant=False, reason="ZERO_SIZE")
            self.attempts.append(row)
            return row

        order_id = hashlib.sha256(f"{aid}|order".encode()).hexdigest()[:32]
        position_id = hashlib.sha256(f"{aid}|position".encode()).hexdigest()[:32]
        fee = quantity * fill_price * self.fee_taker
        position = VirtualPosition(
            position_id=position_id,
            order_id=order_id,
            attempt_id=aid,
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            side=signal.side,
            strategy=signal.strategy,
            opened_at_ms=signal.decision_at_ms,
            entry_price=fill_price,
            stop_price=signal.stop_reference,
            target_price=signal.target_reference,
            quantity=quantity,
            initial_risk_cash=risk_distance * quantity,
            entry_fee=fee,
            remaining_qty=quantity,
            fees=fee,
        )
        self.positions[position_id] = position
        self.equity_cash -= fee
        fill_id = hashlib.sha256(f"{aid}|entry-fill".encode()).hexdigest()[:32]
        self.fills.append(
            {
                "fill_id": fill_id,
                "order_id": order_id,
                "position_id": position_id,
                "attempt_id": aid,
                "signal_id": signal.signal_id,
                "type": "ENTRY",
                "price": fill_price,
                "quantity": quantity,
                "fee": fee,
                "ts_ms": signal.decision_at_ms,
            }
        )
        row.update(
            accepted_by_variant=True,
            reason="OPENED",
            order_id=order_id,
            position_id=position_id,
            quantity=quantity,
            fill_price=fill_price,
            initial_risk_cash=position.initial_risk_cash,
        )
        self.attempts.append(row)
        self._mark()
        return row

    def close(
        self,
        position_id: str,
        *,
        event_id: str,
        price: float,
        quantity: float,
        ts_ms: int,
        reason: str,
        fee_rate: float | None = None,
        funding: float = 0.0,
    ) -> dict[str, Any]:
        if event_id in self._seen_events:
            return {"duplicate": True, "event_id": event_id}
        position = self.positions.get(position_id)
        if position is None or quantity <= 0 or quantity > position.remaining_qty + 1e-12:
            return {"unknown": True, "event_id": event_id}
        if not math.isfinite(price) or price <= 0 or not math.isfinite(funding):
            return {"unknown": True, "event_id": event_id}
        self._seen_events.add(event_id)
        slip = self.slippage_bps / 10_000
        fill_price = price * (
            1 - slip if position.side == "long" else 1 + slip
        )
        exit_quantity = min(quantity, position.remaining_qty)
        gross = (
            (fill_price - position.entry_price)
            * exit_quantity
            * (1 if position.side == "long" else -1)
        )
        fee = fill_price * exit_quantity * (
            self.fee_taker if fee_rate is None else fee_rate
        )
        position.realized_gross += gross
        position.fees += fee
        position.funding += funding
        position.remaining_qty -= exit_quantity
        self.equity_cash += gross - fee + funding
        fill_id = hashlib.sha256(event_id.encode()).hexdigest()[:32]
        self.fills.append(
            {
                "fill_id": fill_id,
                "event_id": event_id,
                "position_id": position_id,
                "type": "EXIT",
                "price": fill_price,
                "quantity": exit_quantity,
                "gross_pnl": gross,
                "fee": fee,
                "funding": funding,
                "ts_ms": ts_ms,
            }
        )
        if position.remaining_qty <= 1e-12:
            position.remaining_qty = 0
            position.status = "CLOSED"
            position.exit_reason = reason
            self.closed.append(position)
            del self.positions[position_id]
        self._mark()
        return {
            "fill_id": fill_id,
            "position_id": position_id,
            "net_fill_pnl": gross - fee + funding,
        }

    def metrics(self, marks: dict[str, float] | None = None) -> dict[str, Any]:
        wins = [p.net_pnl for p in self.closed if p.net_pnl > 0]
        losses = [-p.net_pnl for p in self.closed if p.net_pnl < 0]
        gross_wins = sum(wins)
        gross_losses = sum(losses)
        profit_factor = (
            gross_wins / gross_losses
            if gross_losses
            else (None if not gross_wins else float("inf"))
        )
        net_pnl = sum(position.net_pnl for position in self.closed)
        best = sorted(wins, reverse=True)
        return {
            "variant": self.name,
            "attempts": len(self.attempts),
            "fills": len(self.fills),
            "closed_positions": len(self.closed),
            "open_positions": len(self.positions),
            "net_closed_pnl": net_pnl,
            "equity_including_open_unrealized": self.equity(marks),
            "max_drawdown_pct_observed": self.max_drawdown_pct,
            "profit_factor_closed_positions": profit_factor,
            "R": "UNKNOWN_NOT_IMPLEMENTED",
            "unknown_R_positions": len(self.closed),
            "pnl_without_best_winner": net_pnl - best[0] if best else None,
            "pnl_without_two_best_winners": (
                net_pnl - sum(best[:2]) if len(best) >= 2 else None
            ),
            "pf_contributors": [
                {
                    "position_id": position.position_id,
                    "signal_id": position.signal_id,
                    "net_pnl": position.net_pnl,
                }
                for position in self.closed
            ],
        }

    def export_state(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "config": {
                "initial_capital": self.initial_capital,
                "risk_fraction": self.risk_fraction,
                "max_open": self.max_open,
                "max_exposure_fraction": self.max_exposure_fraction,
                "fee_taker": self.fee_taker,
                "slippage_bps": self.slippage_bps,
                "max_leverage": self.max_leverage,
            },
            "equity_cash": self.equity_cash,
            "peak_equity": self.peak_equity,
            "max_drawdown_pct": self.max_drawdown_pct,
            "positions": [asdict(p) for p in self.positions.values()],
            "closed": [asdict(p) for p in self.closed],
            "attempts": self.attempts,
            "fills": self.fills,
            "seen_events": sorted(self._seen_events),
        }

    @classmethod
    def import_state(cls, state: dict[str, Any]) -> "VirtualPortfolio":
        portfolio = cls(name=state["name"], **state["config"])
        portfolio.equity_cash = float(state["equity_cash"])
        portfolio.peak_equity = float(state["peak_equity"])
        portfolio.max_drawdown_pct = float(state["max_drawdown_pct"])
        portfolio.positions = {
            row["position_id"]: VirtualPosition(**row)
            for row in state.get("positions", [])
        }
        portfolio.closed = [
            VirtualPosition(**row) for row in state.get("closed", [])
        ]
        portfolio.attempts = list(state.get("attempts", []))
        portfolio.fills = list(state.get("fills", []))
        portfolio._seen_events = set(state.get("seen_events", []))
        return portfolio


@dataclass
class ShadowReplay:
    """Event-driven portfolio fan-out; consumes one immutable baseline signal."""

    portfolios: dict[str, VirtualPortfolio] = field(
        default_factory=lambda: {name: VirtualPortfolio(name) for name in "ABC"}
    )
    seen_event_ids: set[str] = field(default_factory=set)

    def apply_signal(
        self,
        *,
        event_id: str,
        signal: Signal,
        zone: ZoneDecision,
        execution_price: float | None,
    ) -> dict[str, Any]:
        if event_id in self.seen_event_ids:
            return {"duplicate": True, "event_id": event_id}
        self.seen_event_ids.add(event_id)
        results = {}
        for name in ("A", "B", "C"):
            decision = variant_decision(name, signal, zone)
            results[name] = self.portfolios[name].attempt(
                signal,
                decision,
                execution_price=execution_price,
                attempt_id=f"{event_id}:{name}",
            )
        return {
            "event_id": event_id,
            "signal_id": signal.signal_id,
            "variant_results": results,
        }

    def apply_exit(
        self,
        *,
        variant: str,
        position_id: str,
        event_id: str,
        price: float,
        quantity: float,
        ts_ms: int,
        reason: str,
        funding: float = 0.0,
    ) -> dict[str, Any]:
        if variant not in self.portfolios:
            raise ValueError(f"unknown variant {variant}")
        if event_id in self.seen_event_ids:
            return {"duplicate": True, "event_id": event_id}
        result = self.portfolios[variant].close(
            position_id,
            event_id=event_id,
            price=price,
            quantity=quantity,
            ts_ms=ts_ms,
            reason=reason,
            funding=funding,
        )
        if not result.get("unknown"):
            self.seen_event_ids.add(event_id)
        return result

    def export_state(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "seen_event_ids": sorted(self.seen_event_ids),
            "portfolios": {
                name: portfolio.export_state()
                for name, portfolio in self.portfolios.items()
            },
        }

    @classmethod
    def import_state(cls, state: dict[str, Any]) -> "ShadowReplay":
        if state.get("schema_version") != 1:
            raise ValueError("unsupported replay state schema")
        portfolios = {
            name: VirtualPortfolio.import_state(row)
            for name, row in state["portfolios"].items()
        }
        if set(portfolios) != {"A", "B", "C"}:
            raise ValueError("replay state must contain A, B and C")
        return cls(portfolios, set(state.get("seen_event_ids", [])))
