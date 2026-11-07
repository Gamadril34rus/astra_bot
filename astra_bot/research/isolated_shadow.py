"""Isolated ZEUS research replay primitives; no live wiring or exchange client imports."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import math
from typing import Any


MSK = timezone(timedelta(hours=3))


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
class ExecutionQuote:
    """Timestamped best bid/ask and displayed size from an independent quote tape."""

    quote_id: str
    timestamp_ms: int
    received_at_ms: int
    bid: float | None
    ask: float | None
    bid_size: float | None
    ask_size: float | None


@dataclass(frozen=True)
class ReplayBar:
    """One OHLC bar; processing is permitted only after its close is observable."""

    symbol: str
    timeframe: str
    open_time_ms: int
    close_time_ms: int
    received_at_ms: int
    open: float
    high: float
    low: float
    close: float
    closed: bool = True


@dataclass(frozen=True)
class ExitIntent:
    portfolio: str
    position_id: str
    intent_id: str
    reason: str
    reference_price: float
    triggered_at_ms: int
    requested_quantity: float
    tp_level_index: int | None = None


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
    state = ZoneState.OUTSIDE_NEAR if distance <= threshold_pct else ZoneState.OUTSIDE_FAR
    return ZoneDecision(state, boundary, distance, "boundary_distance")


def select_baseline_signal(
    channel: dict[str, Any] | None,
    multi_4h: dict[str, Any] | None,
    multi_1h: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """One shared source event: channel first, then 4h and 1h multi fallback."""
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


def next_available_quote(
    quotes: list[ExecutionQuote],
    *,
    strictly_after_ms: int,
    max_delivery_lag_ms: int = 30_000,
    max_wait_ms: int = 2_700_000,
) -> ExecutionQuote | None:
    """Return the first usable chronological quote strictly after the event.

    The earliest quote is not silently skipped when its timestamp/receive data
    are inconsistent. Callers must treat None as UNKNOWN, not as a fill.
    """
    ordered = sorted(quotes, key=lambda quote: quote.timestamp_ms)
    candidates = [quote for quote in ordered if quote.timestamp_ms > strictly_after_ms]
    if not candidates:
        return None
    quote = candidates[0]
    if quote.received_at_ms < quote.timestamp_ms:
        return None
    if quote.received_at_ms - quote.timestamp_ms > max_delivery_lag_ms:
        return None
    if quote.timestamp_ms - strictly_after_ms > max_wait_ms:
        return None
    return quote


def executable_side(
    quote: ExecutionQuote, *, position_side: str, action: str
) -> tuple[float | None, float | None]:
    """Return executable-side price and displayed quantity; no mid-price substitution."""
    if position_side not in {"long", "short"} or action not in {"entry", "exit"}:
        return None, None
    use_ask = (position_side == "long" and action == "entry") or (
        position_side == "short" and action == "exit"
    )
    price = quote.ask if use_ask else quote.bid
    quantity = quote.ask_size if use_ask else quote.bid_size
    if price is None or not math.isfinite(price) or price <= 0:
        return None, None
    if quantity is None or not math.isfinite(quantity) or quantity <= 0:
        return float(price), None
    return float(price), float(quantity)


def session_profile(timestamp_ms: int) -> dict[str, Any]:
    """Exact hour/day buckets from live_session.py (MSK); not an env override."""
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).astimezone(MSK)
    hour = dt.hour
    weekend = dt.weekday() >= 5
    if weekend:
        if hour in (16, 17, 18, 19):
            name = "normal"
        elif hour in (10, 11, 12, 15, 20, 21):
            name = "thin"
        else:
            name = "off"
    elif hour in (10, 11, 12, 16, 17, 18, 19, 20):
        name = "fat"
    elif hour in (3, 4, 5, 6, 13, 14, 15, 21, 22):
        name = "normal"
    elif hour in (0, 1, 2, 23):
        name = "thin"
    else:
        name = "off"
    presets = {
        "fat": (True, 0.50, 0.70),
        "normal": (True, 0.40, 0.55),
        "thin": (True, 0.30, 0.45),
        "off": (False, 0.30, 0.45),
    }
    arm, be_trigger_r, tp1_rr = presets[name]
    return {
        "name": name,
        "arm": arm,
        "be_trigger_r": be_trigger_r,
        "tp1_rr": tp1_rr,
        "hour_msk": hour,
        "weekday_msk": dt.weekday(),
    }


def confidence_risk_fraction(confidence: float | None, *, heat_active: bool) -> float:
    """Exact conf_risk_pct ladder; heat scales the selected tier by 0.5 in live_arm."""
    if confidence is None:
        fraction = 0.01
    elif confidence >= 0.88:
        fraction = 0.03
    elif confidence >= 0.80:
        fraction = 0.02
    elif confidence >= 0.70:
        fraction = 0.015
    else:
        fraction = 0.01
    return fraction * (0.5 if heat_active else 1.0)


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
    closed_at_ms: int | None = None
    funding_complete: bool = False
    funding_coverage_through_ms: int | None = None
    tp_levels: list[dict[str, float]] = field(default_factory=list)
    tp_filled_qty: list[float] = field(default_factory=list)
    pending_exit: dict[str, Any] | None = None
    highest_price: float | None = None
    lowest_price: float | None = None
    bars_held: int = 0

    @property
    def net_pnl(self) -> float:
        return self.realized_gross - self.fees + self.funding


@dataclass
class VirtualPortfolio:
    """Single counterfactual portfolio with its own state and virtual safety gates."""

    name: str
    initial_capital: float = 100_000.0
    risk_fraction: float = 0.01
    max_open: int = 5
    fee_taker: float = 0.0005
    max_leverage: float = 6.0
    max_position_value_usdt: float = 50_000.0
    cooldown_ms: int = 20 * 60 * 1000
    same_side_cluster_max: int = 4
    inj_slot_reserve: int = 2
    day_loss_limit: float = 0.04
    week_loss_limit: float = 0.06
    halt_pf_min_n: int = 5
    halt_pf_threshold: float = 1.0
    halt_pf_window: int = 20
    heat_loss_threshold: int = 3
    session_gate_enabled: bool = True
    equity_cash: float = field(init=False)
    positions: dict[str, VirtualPosition] = field(default_factory=dict)
    closed: list[VirtualPosition] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    fills: list[dict[str, Any]] = field(default_factory=list)
    peak_equity: float = field(init=False)
    max_drawdown_pct: float = 0.0
    _seen_events: set[str] = field(default_factory=set)
    last_entry_by_key: dict[str, int] = field(default_factory=dict)
    consecutive_losses: int = 0
    heat_active: bool = False
    heat_day_utc: str | None = None
    halted: bool = False
    halt_reason: str | None = None
    risk_data_unknown: bool = False
    last_mark_timestamp_ms: int | None = None
    last_mark_equity: float | None = None

    def __post_init__(self):
        self.equity_cash = float(self.initial_capital)
        self.peak_equity = self.equity_cash

    def _expire_heat(self, timestamp_ms: int):
        current_day = datetime.fromtimestamp(
            timestamp_ms / 1000, tz=UTC
        ).date().isoformat()
        if self.heat_active and self.heat_day_utc and current_day > self.heat_day_utc:
            self.heat_active = False
            self.heat_day_utc = None

    def _utc_period_start(self, timestamp_ms: int, *, weekly: bool) -> int:
        dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        if weekly:
            start = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
            start -= timedelta(days=start.weekday())
        else:
            start = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
        return int(start.timestamp() * 1000)

    def _cash_flow_since(self, timestamp_ms: int) -> float:
        return sum(
            float(fill["cash_delta"])
            for fill in self.fills
            if fill.get("cash_delta") is not None
            and int(fill.get("ts_ms", 0)) >= timestamp_ms
        )

    def _risk_gate(self, signal: Signal, timestamp_ms: int) -> tuple[str | None, float]:
        self._expire_heat(timestamp_ms)
        if self.halted:
            return "VIRTUAL_HALT", 0.0
        if self.risk_data_unknown:
            return "UNKNOWN_RISK_DATA", 0.0
        profile = session_profile(timestamp_ms) if self.session_gate_enabled else {
            "name": "session_disabled",
            "arm": True,
            "tp1_rr": 0.6,
            "be_trigger_r": 0.5,
        }
        if not profile["arm"]:
            return "SESSION_OFF", 0.0
        if len(self.positions) >= self.max_open:
            return "MAX_OPEN", 0.0
        if any(position.symbol == signal.symbol for position in self.positions.values()):
            return "SYMBOL_ALREADY_OPEN", 0.0
        open_count = len(self.positions)
        if signal.symbol.upper() == "INJ-USDT" and open_count > self.max_open - self.inj_slot_reserve:
            return "INJ_SLOT_RESERVE", 0.0
        same_side = sum(
            1 for position in self.positions.values() if position.side == signal.side
        )
        if same_side >= self.same_side_cluster_max:
            return "SAME_SIDE_CLUSTER_MAX", 0.0
        key = f"{signal.strategy}|{signal.symbol}|{signal.side}"
        last_entry = self.last_entry_by_key.get(key)
        if last_entry is not None and timestamp_ms - last_entry < self.cooldown_ms:
            return "ENTRY_COOLDOWN", 0.0

        equity = self.equity_cash
        if equity <= 0:
            return "NON_POSITIVE_EQUITY", 0.0
        day_pnl = self._cash_flow_since(self._utc_period_start(timestamp_ms, weekly=False))
        week_pnl = self._cash_flow_since(self._utc_period_start(timestamp_ms, weekly=True))
        if day_pnl <= -equity * self.day_loss_limit:
            return "VIRTUAL_DAY_LOSS_LIMIT", 0.0
        if week_pnl <= -equity * self.week_loss_limit:
            return "VIRTUAL_WEEK_LOSS_LIMIT", 0.0
        return None, confidence_risk_fraction(signal.confidence, heat_active=self.heat_active)

    def attempt(
        self,
        signal: Signal,
        decision: VariantDecision,
        *,
        execution_price: float | None = None,
        execution_quantity: float | None = None,
        execution_timestamp_ms: int | None = None,
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
        if (
            execution_timestamp_ms is None
            or execution_timestamp_ms <= signal.decision_at_ms
        ):
            row.update(accepted_by_variant=False, reason="UNKNOWN_EXECUTION_TIMESTAMP")
            self.attempts.append(row)
            return row
        if execution_price is None or not math.isfinite(execution_price) or execution_price <= 0:
            row.update(accepted_by_variant=False, reason="UNKNOWN_EXECUTION_PRICE")
            self.attempts.append(row)
            return row
        if execution_quantity is None or not math.isfinite(execution_quantity) or execution_quantity <= 0:
            row.update(accepted_by_variant=False, reason="UNKNOWN_EXECUTION_LIQUIDITY")
            self.attempts.append(row)
            return row

        gate_reason, risk_fraction = self._risk_gate(signal, execution_timestamp_ms)
        if gate_reason:
            row.update(accepted_by_variant=False, reason=gate_reason)
            self.attempts.append(row)
            return row
        invalid_stop = (
            signal.side == "long" and signal.stop_reference >= execution_price
        ) or (
            signal.side == "short" and signal.stop_reference <= execution_price
        )
        invalid_target = signal.target_reference is not None and (
            (signal.side == "long" and signal.target_reference <= execution_price)
            or (signal.side == "short" and signal.target_reference >= execution_price)
        )
        if signal.side not in {"long", "short"} or invalid_stop or invalid_target:
            row.update(accepted_by_variant=False, reason="INVALID_INITIAL_RISK")
            self.attempts.append(row)
            return row

        risk_distance = abs(execution_price - signal.stop_reference)
        if risk_distance <= 0:
            row.update(accepted_by_variant=False, reason="INVALID_INITIAL_RISK")
            self.attempts.append(row)
            return row
        risk_cash = self.equity_cash * risk_fraction
        desired_quantity = risk_cash / risk_distance
        desired_quantity = min(
            desired_quantity,
            self.equity_cash * self.max_leverage / execution_price,
            self.max_position_value_usdt / execution_price,
        )
        quantity = min(desired_quantity, execution_quantity)
        if quantity <= 0:
            row.update(accepted_by_variant=False, reason="ZERO_SIZE")
            self.attempts.append(row)
            return row

        order_id = hashlib.sha256(f"{aid}|order".encode()).hexdigest()[:32]
        position_id = hashlib.sha256(f"{aid}|position".encode()).hexdigest()[:32]
        fee = quantity * execution_price * self.fee_taker
        raw_levels = signal.metadata.get("take_profit_levels")
        if raw_levels is not None:
            levels = [
                {"price": float(level["price"]), "fraction": float(level["fraction"])}
                for level in raw_levels
            ]
        else:
            # The live arm sends a session-profile TP1 at 30%; the remaining
            # runner is managed by the separate live exit-engine (not fully
            # reconstructed by this bracket-only OHLC replay).
            profile = (
                session_profile(execution_timestamp_ms)
                if self.session_gate_enabled
                else {"tp1_rr": 0.6}
            )
            tp1 = execution_price + (
                risk_distance * float(profile["tp1_rr"])
                * (1 if signal.side == "long" else -1)
            )
            levels = [{"price": tp1, "fraction": 0.30}]
        if any(
            not math.isfinite(level["price"])
            or not math.isfinite(level["fraction"])
            or level["fraction"] <= 0
            or level["fraction"] > 1
            or (
                signal.side == "long" and level["price"] <= execution_price
            )
            or (
                signal.side == "short" and level["price"] >= execution_price
            )
            for level in levels
        ) or sum(level["fraction"] for level in levels) > 1.0 + 1e-12:
            row.update(accepted_by_variant=False, reason="INVALID_TP_SCHEDULE")
            self.attempts.append(row)
            return row

        position = VirtualPosition(
            position_id=position_id,
            order_id=order_id,
            attempt_id=aid,
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            side=signal.side,
            strategy=signal.strategy,
            opened_at_ms=execution_timestamp_ms,
            entry_price=execution_price,
            stop_price=signal.stop_reference,
            target_price=signal.target_reference,
            quantity=quantity,
            initial_risk_cash=risk_distance * quantity,
            entry_fee=fee,
            remaining_qty=quantity,
            fees=fee,
            tp_levels=levels,
            tp_filled_qty=[0.0 for _ in levels],
            highest_price=execution_price,
            lowest_price=execution_price,
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
                "price": execution_price,
                "quantity": quantity,
                "fee": fee,
                "cash_delta": -fee,
                "ts_ms": execution_timestamp_ms,
                "fee_model": "configured_taker_assumption",
            }
        )
        key = f"{signal.strategy}|{signal.symbol}|{signal.side}"
        self.last_entry_by_key[key] = execution_timestamp_ms
        partial = quantity + 1e-12 < desired_quantity
        row.update(
            accepted_by_variant=True,
            reason="OPENED_PARTIAL" if partial else "OPENED",
            order_id=order_id,
            position_id=position_id,
            quantity=quantity,
            requested_quantity=desired_quantity,
            fill_price=execution_price,
            initial_risk_cash=position.initial_risk_cash,
            risk_fraction=risk_fraction,
            fee=fee,
        )
        self.attempts.append(row)
        return row

    def apply_funding_event(
        self,
        position_id: str,
        *,
        event_id: str,
        cash_amount: float | None,
        ts_ms: int,
    ) -> dict[str, Any]:
        if event_id in self._seen_events:
            return {"duplicate": True, "event_id": event_id}
        position = self.positions.get(position_id)
        if position is None:
            return {"unknown": True, "reason": "position_not_open"}
        self._seen_events.add(event_id)
        if cash_amount is None or not math.isfinite(cash_amount):
            position.funding_complete = False
            self.risk_data_unknown = True
            return {"unknown": True, "reason": "funding_amount_missing"}
        position.funding += cash_amount
        self.equity_cash += cash_amount
        self.fills.append(
            {
                "fill_id": hashlib.sha256(event_id.encode()).hexdigest()[:32],
                "event_id": event_id,
                "position_id": position_id,
                "type": "FUNDING",
                "funding": cash_amount,
                "cash_delta": cash_amount,
                "ts_ms": ts_ms,
            }
        )
        return {"applied": True, "funding": cash_amount}

    def set_funding_coverage(
        self,
        position_id: str,
        *,
        through_ms: int,
        cumulative_cash_amount: float,
        source_verified: bool,
    ) -> dict[str, Any]:
        """Declare full lifecycle funding coverage only after source reconciliation."""
        position = self.positions.get(position_id)
        is_closed = False
        if position is None:
            position = next(
                (item for item in self.closed if item.position_id == position_id),
                None,
            )
            is_closed = position is not None
        if position is None:
            return {"unknown": True, "reason": "position_not_found"}
        if (
            not source_verified
            or not math.isfinite(cumulative_cash_amount)
            or through_ms < position.opened_at_ms
            or (
                is_closed
                and (
                    position.closed_at_ms is None
                    or through_ms < position.closed_at_ms
                )
            )
        ):
            return {"unknown": True, "reason": "funding_coverage_unverified"}
        delta = cumulative_cash_amount - position.funding
        position.funding = cumulative_cash_amount
        position.funding_complete = True
        position.funding_coverage_through_ms = through_ms
        self.equity_cash += delta
        self.risk_data_unknown = any(
            not closed.funding_complete for closed in self.closed
        ) or any(
            not open_position.funding_complete
            for open_position in self.positions.values()
        )
        if is_closed:
            self._after_close(position, through_ms)
        return {"covered": True, "funding_total": cumulative_cash_amount}

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
    ) -> dict[str, Any]:
        if event_id in self._seen_events:
            return {"duplicate": True, "event_id": event_id}
        position = self.positions.get(position_id)
        if position is None or quantity <= 0 or quantity > position.remaining_qty + 1e-12:
            return {"unknown": True, "event_id": event_id}
        if not math.isfinite(price) or price <= 0:
            return {"unknown": True, "event_id": event_id}
        self._seen_events.add(event_id)
        exit_quantity = min(quantity, position.remaining_qty)
        gross = (
            (price - position.entry_price)
            * exit_quantity
            * (1 if position.side == "long" else -1)
        )
        applied_fee_rate = self.fee_taker if fee_rate is None else fee_rate
        if not math.isfinite(applied_fee_rate) or applied_fee_rate < 0:
            self._seen_events.remove(event_id)
            return {"unknown": True, "reason": "invalid_fee_model"}
        fee = price * exit_quantity * applied_fee_rate
        position.realized_gross += gross
        position.fees += fee
        position.remaining_qty -= exit_quantity
        self.equity_cash += gross - fee
        fill_id = hashlib.sha256(event_id.encode()).hexdigest()[:32]
        self.fills.append(
            {
                "fill_id": fill_id,
                "event_id": event_id,
                "position_id": position_id,
                "type": "EXIT",
                "price": price,
                "quantity": exit_quantity,
                "gross_pnl": gross,
                "fee": fee,
                "cash_delta": gross - fee,
                "ts_ms": ts_ms,
                "fee_model": "configured_taker_assumption",
            }
        )
        if position.remaining_qty <= 1e-12:
            position.remaining_qty = 0
            position.status = "CLOSED"
            position.exit_reason = reason
            position.closed_at_ms = ts_ms
            if (
                position.funding_coverage_through_ms is None
                or position.funding_coverage_through_ms < ts_ms
            ):
                position.funding_complete = False
            self.closed.append(position)
            del self.positions[position_id]
            self._after_close(position, ts_ms)
        return {
            "fill_id": fill_id,
            "position_id": position_id,
            "net_fill_pnl_before_funding": gross - fee,
            "remaining_qty": position.remaining_qty if position_id in self.positions else 0.0,
        }

    def _after_close(self, position: VirtualPosition, ts_ms: int):
        self.risk_data_unknown = any(
            not closed.funding_complete for closed in self.closed
        )
        completed = [
            closed for closed in self.closed if closed.funding_complete
        ]
        streak = 0
        heat_active = False
        heat_day = None
        for closed in completed:
            closed_ms = closed.closed_at_ms or ts_ms
            day = datetime.fromtimestamp(
                closed_ms / 1000, tz=timezone.utc
            ).date().isoformat()
            if heat_active and heat_day and day > heat_day:
                heat_active = False
                heat_day = None
            if closed.net_pnl < 0:
                streak += 1
                if streak >= self.heat_loss_threshold and not heat_active:
                    heat_active = True
                    heat_day = day
            elif closed.net_pnl > 0:
                streak = 0
                heat_active = False
                heat_day = None
        self.consecutive_losses = streak
        self.heat_active = heat_active
        self.heat_day_utc = heat_day

        if self.halted:
            return
        sample = [closed.net_pnl for closed in completed][-self.halt_pf_window:]
        if not self.risk_data_unknown and len(sample) >= self.halt_pf_min_n:
            winners = sum(value for value in sample if value > 0)
            losers = -sum(value for value in sample if value < 0)
            pf = winners / losers if losers > 0 else (
                math.inf if winners > 0 else None
            )
            if pf is not None and pf < self.halt_pf_threshold:
                self.halted = True
                self.halt_reason = (
                    f"PF={pf:.3f} n={len(sample)} < "
                    f"{self.halt_pf_threshold:.3f} (virtual window={self.halt_pf_window})"
                )

    def process_bar(self, bar: ReplayBar) -> list[ExitIntent]:
        """Create exit intents from a closed OHLC bar; never fabricate an execution fill."""
        if (
            not bar.closed
            or bar.received_at_ms < bar.close_time_ms
            or bar.close_time_ms <= bar.open_time_ms
            or not all(
                math.isfinite(value)
                and value > 0
                for value in (bar.open, bar.high, bar.low, bar.close)
            )
            or bar.high < max(bar.open, bar.close, bar.low)
            or bar.low > min(bar.open, bar.close, bar.high)
        ):
            return []
        intents = []
        for position in list(self.positions.values()):
            if (
                position.symbol != bar.symbol
                or bar.close_time_ms <= position.opened_at_ms
                or bar.open_time_ms < position.opened_at_ms
            ):
                # A bar that straddles the entry timestamp contains pre-entry
                # extremes. It cannot be used without a finer-grained tape.
                continue
            if position.pending_exit is not None:
                continue
            side = position.side
            stop_hit = (
                bar.open <= position.stop_price
                if side == "long"
                else bar.open >= position.stop_price
            )
            stop_touch = (
                bar.low <= position.stop_price
                if side == "long"
                else bar.high >= position.stop_price
            )
            tp_index = next(
                (
                    index
                    for index, level in enumerate(position.tp_levels)
                    if position.tp_filled_qty[index]
                    < position.quantity * level["fraction"] - 1e-12
                ),
                None,
            )
            tp_price = (
                position.tp_levels[tp_index]["price"]
                if tp_index is not None
                else None
            )
            tp_gap = tp_price is not None and (
                (bar.open >= tp_price) if side == "long" else (bar.open <= tp_price)
            )
            tp_touch = tp_price is not None and (
                (bar.high >= tp_price) if side == "long" else (bar.low <= tp_price)
            )

            reason = None
            reference = None
            selected_tp_index = None
            requested = position.remaining_qty
            # Stop always wins a same-candle collision. A stop gap is priced at
            # the adverse open; intrabar touch uses the stop trigger reference.
            if stop_hit:
                reason = "STOP_GAP"
                reference = bar.open
            elif tp_gap:
                # Opening beyond a resting TP establishes the first touch at
                # the open; do not mislabel a later same-bar reversal as ambiguous.
                reason = "TAKE_PROFIT_GAP"
                reference = tp_price
                selected_tp_index = tp_index
                level = position.tp_levels[tp_index]
                level_remaining = (
                    position.quantity * level["fraction"]
                    - position.tp_filled_qty[tp_index]
                )
                requested = min(position.remaining_qty, level_remaining)
            elif stop_touch and tp_touch:
                reason = "STOP_FIRST_AMBIGUOUS_BAR"
                reference = position.stop_price
            elif stop_touch:
                reason = "STOP"
                reference = position.stop_price
            elif tp_gap or tp_touch:
                reason = "TAKE_PROFIT_GAP" if tp_gap else "TAKE_PROFIT"
                reference = tp_price
                selected_tp_index = tp_index
                level = position.tp_levels[tp_index]
                level_remaining = (
                    position.quantity * level["fraction"]
                    - position.tp_filled_qty[tp_index]
                )
                requested = min(position.remaining_qty, level_remaining)
            elif bar.close_time_ms - position.opened_at_ms >= 48 * 60 * 60 * 1000:
                reason = "MAX_HOLD_48H"
                reference = bar.close

            position.highest_price = max(position.highest_price or position.entry_price, bar.high)
            position.lowest_price = min(position.lowest_price or position.entry_price, bar.low)
            position.bars_held += 1
            if reason is None or reference is None or requested <= 0:
                continue
            intent_id = hashlib.sha256(
                f"{self.name}|{position.position_id}|{bar.close_time_ms}|{reason}|{selected_tp_index}".encode()
            ).hexdigest()[:32]
            pending = {
                "intent_id": intent_id,
                "reason": reason,
                "reference_price": reference,
                "triggered_at_ms": bar.close_time_ms,
                "after_ms": bar.close_time_ms,
                "quantity_remaining": requested,
                "tp_level_index": selected_tp_index,
            }
            position.pending_exit = pending
            intents.append(
                ExitIntent(
                    self.name,
                    position.position_id,
                    intent_id,
                    reason,
                    reference,
                    bar.close_time_ms,
                    requested,
                    selected_tp_index,
                )
            )
        return intents

    def execute_pending_exit(
        self,
        position_id: str,
        quotes: list[ExecutionQuote],
        *,
        max_delivery_lag_ms: int = 30_000,
    ) -> dict[str, Any]:
        """Execute an exit only from a later executable-side quote and displayed size."""
        position = self.positions.get(position_id)
        if position is None or position.pending_exit is None:
            return {"unknown": True, "reason": "no_pending_exit"}
        pending = position.pending_exit
        quote = next_available_quote(
            quotes,
            strictly_after_ms=int(pending["after_ms"]),
            max_delivery_lag_ms=max_delivery_lag_ms,
            max_wait_ms=2_700_000,
        )
        if quote is None or not quote.quote_id:
            return {"unknown": True, "reason": "UNKNOWN_EXIT_EXECUTION_PRICE"}
        if (
            quote.timestamp_ms - int(pending["triggered_at_ms"]) > 2_700_000
        ):
            return {"unknown": True, "reason": "EXIT_EXECUTION_WINDOW_EXPIRED"}
        price, displayed_quantity = executable_side(
            quote, position_side=position.side, action="exit"
        )
        if price is None:
            return {"unknown": True, "reason": "UNKNOWN_EXIT_EXECUTION_PRICE"}
        if displayed_quantity is None:
            return {"unknown": True, "reason": "UNKNOWN_EXIT_LIQUIDITY"}
        tp_index_for_limit = pending.get("tp_level_index")
        if tp_index_for_limit is not None:
            target_price = float(
                position.tp_levels[int(tp_index_for_limit)]["price"]
            )
            marketable = (
                price >= target_price if position.side == "long"
                else price <= target_price
            )
            if not marketable:
                pending["after_ms"] = quote.timestamp_ms
                return {
                    "unknown": True,
                    "reason": "TP_LIMIT_NOT_MARKETABLE_YET",
                    "next_after_ms": quote.timestamp_ms,
                }
        requested_before = float(pending["quantity_remaining"])
        quantity = min(requested_before, displayed_quantity)
        if quantity <= 0:
            return {"unknown": True, "reason": "UNKNOWN_EXIT_LIQUIDITY"}
        event_id = f"{pending['intent_id']}:{quote.quote_id}"
        result = self.close(
            position_id,
            event_id=event_id,
            price=price,
            quantity=quantity,
            ts_ms=quote.timestamp_ms,
            reason=str(pending["reason"]),
            fee_rate=self.fee_taker,
        )
        if result.get("unknown") or result.get("duplicate"):
            return result
        current = self.positions.get(position_id)
        if current is not None:
            current_pending = current.pending_exit
            current_pending["quantity_remaining"] = max(
                0.0, float(current_pending["quantity_remaining"]) - quantity
            )
            current_pending["after_ms"] = quote.timestamp_ms
            tp_index = current_pending.get("tp_level_index")
            if tp_index is not None:
                current.tp_filled_qty[tp_index] += quantity
            if current_pending["quantity_remaining"] <= 1e-12:
                current.pending_exit = None
        return {
            "filled_quantity": quantity,
            "partial": quantity + 1e-12 < requested_before,
            "quote_id": quote.quote_id,
            "fill": result,
        }

    def mark_to_market(
        self,
        marks: dict[str, float],
        *,
        timestamp_ms: int,
    ) -> dict[str, Any]:
        """Update peak/drawdown only when every open position has a valid mark/funding coverage."""
        unrealized = 0.0
        unknown = []
        for position in self.positions.values():
            mark = marks.get(position.symbol)
            if mark is None or not math.isfinite(mark) or mark <= 0:
                unknown.append(f"{position.symbol}:mark")
                continue
            if (
                position.funding_coverage_through_ms is None
                or position.funding_coverage_through_ms < timestamp_ms
            ):
                unknown.append(f"{position.symbol}:funding")
            gross = (
                (mark - position.entry_price)
                * position.remaining_qty
                * (1 if position.side == "long" else -1)
            )
            estimated_exit_fee = mark * position.remaining_qty * self.fee_taker
            unrealized += gross - estimated_exit_fee
        if unknown:
            self.last_mark_timestamp_ms = timestamp_ms
            self.last_mark_equity = None
            return {"known": False, "unknown": unknown, "equity": None}
        equity = self.equity_cash + unrealized
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity > 0:
            drawdown = (self.peak_equity - equity) / self.peak_equity * 100
            self.max_drawdown_pct = max(self.max_drawdown_pct, drawdown)
        self.last_mark_timestamp_ms = timestamp_ms
        self.last_mark_equity = equity
        return {"known": True, "equity": equity, "drawdown_pct": self.max_drawdown_pct}

    def metrics(
        self,
        marks: dict[str, float] | None = None,
        *,
        mark_timestamp_ms: int | None = None,
    ) -> dict[str, Any]:
        complete_closed = [position for position in self.closed if position.funding_complete]
        incomplete_closed = len(self.closed) - len(complete_closed)
        closed_values = [position.net_pnl for position in complete_closed]
        winners = sum(value for value in closed_values if value > 0)
        losers = -sum(value for value in closed_values if value < 0)
        closed_pf = winners / losers if losers else (
            math.inf if winners > 0 else None
        )
        closed_r = [
            position.net_pnl / position.initial_risk_cash
            for position in complete_closed
            if position.initial_risk_cash > 0
        ]

        marked_values = list(closed_values)
        open_mark_unknown = (
            ["closed_position_funding_unknown"] if incomplete_closed else []
        )
        if self.positions:
            if marks is None or mark_timestamp_ms is None:
                open_mark_unknown.append("open_positions_not_marked")
            else:
                for position in self.positions.values():
                    mark = marks.get(position.symbol)
                    if mark is None or mark <= 0:
                        open_mark_unknown.append(f"{position.symbol}:mark")
                        continue
                    if (
                        position.funding_coverage_through_ms is None
                        or position.funding_coverage_through_ms < mark_timestamp_ms
                    ):
                        open_mark_unknown.append(f"{position.symbol}:funding")
                        continue
                    unrealized = (
                        (mark - position.entry_price)
                        * position.remaining_qty
                        * (1 if position.side == "long" else -1)
                    )
                    estimated_exit_fee = mark * position.remaining_qty * self.fee_taker
                    marked_values.append(
                        position.realized_gross
                        + unrealized
                        - position.fees
                        - estimated_exit_fee
                        + position.funding
                    )
        mtm_pf = None
        if not open_mark_unknown and len(marked_values) > 0:
            mtm_wins = sum(value for value in marked_values if value > 0)
            mtm_losses = -sum(value for value in marked_values if value < 0)
            mtm_pf = mtm_wins / mtm_losses if mtm_losses else (
                math.inf if mtm_wins > 0 else None
            )
        open_r = []
        if not open_mark_unknown and marks is not None:
            for position in self.positions.values():
                mark = marks[position.symbol]
                gross = (
                    (mark - position.entry_price)
                    * position.remaining_qty
                    * (1 if position.side == "long" else -1)
                )
                estimated_exit_fee = mark * position.remaining_qty * self.fee_taker
                net = (
                    position.realized_gross + gross - position.fees
                    - estimated_exit_fee + position.funding
                )
                if position.initial_risk_cash > 0:
                    open_r.append(net / position.initial_risk_cash)

        return {
            "variant": self.name,
            "attempts": len(self.attempts),
            "fills": len(self.fills),
            "closed_positions": len(self.closed),
            "open_positions": len(self.positions),
            "net_closed_pnl_complete_only": sum(closed_values),
            "closed_pf_complete_positions_only": (
                closed_pf if incomplete_closed == 0 else None
            ),
            "closed_pf_sample_complete": incomplete_closed == 0,
            "incomplete_funding_closed_positions": incomplete_closed,
            "mean_closed_R_complete_only": (
                sum(closed_r) / len(closed_r) if closed_r else None
            ),
            "closed_R_values": closed_r,
            "open_R_values_marked": open_r if not open_mark_unknown else None,
            "mark_to_market_pf_including_open": mtm_pf,
            "mark_to_market_status": "UNKNOWN" if open_mark_unknown else "KNOWN",
            "mark_to_market_unknown": open_mark_unknown,
            "equity_cash": self.equity_cash,
            "last_mark_equity": self.last_mark_equity,
            "last_mark_timestamp_ms": self.last_mark_timestamp_ms,
            "max_drawdown_pct_marked": self.max_drawdown_pct,
            "drawdown_status": (
                "UNKNOWN" if incomplete_closed or open_mark_unknown else "KNOWN"
            ),
            "virtual_halted": self.halted,
            "virtual_halt_reason": self.halt_reason,
            "heat_active": self.heat_active,
            "risk_data_unknown": self.risk_data_unknown,
            "pf_contributors": [
                {
                    "position_id": position.position_id,
                    "signal_id": position.signal_id,
                    "net_pnl": position.net_pnl,
                    "R": (
                        position.net_pnl / position.initial_risk_cash
                        if position.initial_risk_cash > 0
                        else None
                    ),
                }
                for position in complete_closed
            ],
        }

    def export_state(self) -> dict[str, Any]:
        config_keys = (
            "initial_capital",
            "risk_fraction",
            "max_open",
            "fee_taker",
            "max_leverage",
            "max_position_value_usdt",
            "cooldown_ms",
            "same_side_cluster_max",
            "inj_slot_reserve",
            "day_loss_limit",
            "week_loss_limit",
            "halt_pf_min_n",
            "halt_pf_threshold",
            "halt_pf_window",
            "heat_loss_threshold",
            "session_gate_enabled",
        )
        return {
            "name": self.name,
            "config": {key: getattr(self, key) for key in config_keys},
            "equity_cash": self.equity_cash,
            "peak_equity": self.peak_equity,
            "max_drawdown_pct": self.max_drawdown_pct,
            "positions": [asdict(position) for position in self.positions.values()],
            "closed": [asdict(position) for position in self.closed],
            "attempts": self.attempts,
            "fills": self.fills,
            "seen_events": sorted(self._seen_events),
            "last_entry_by_key": self.last_entry_by_key,
            "consecutive_losses": self.consecutive_losses,
            "heat_active": self.heat_active,
            "heat_day_utc": self.heat_day_utc,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "risk_data_unknown": self.risk_data_unknown,
            "last_mark_timestamp_ms": self.last_mark_timestamp_ms,
            "last_mark_equity": self.last_mark_equity,
        }

    @classmethod
    def import_state(cls, state: dict[str, Any]) -> VirtualPortfolio:
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
        portfolio.last_entry_by_key = {
            str(key): int(value)
            for key, value in state.get("last_entry_by_key", {}).items()
        }
        portfolio.consecutive_losses = int(state.get("consecutive_losses", 0))
        portfolio.heat_active = bool(state.get("heat_active", False))
        portfolio.heat_day_utc = state.get("heat_day_utc")
        portfolio.halted = bool(state.get("halted", False))
        portfolio.halt_reason = state.get("halt_reason")
        portfolio.risk_data_unknown = bool(state.get("risk_data_unknown", False))
        portfolio.last_mark_timestamp_ms = state.get("last_mark_timestamp_ms")
        portfolio.last_mark_equity = state.get("last_mark_equity")
        return portfolio


@dataclass
class ShadowReplay:
    """Shared source-event fan-out into independent A/B/C counterfactuals."""

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
        execution_quantity: float | None = None,
        execution_timestamp_ms: int | None = None,
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
                execution_quantity=execution_quantity,
                execution_timestamp_ms=execution_timestamp_ms,
                attempt_id=f"{event_id}:{name}",
            )
        return {
            "event_id": event_id,
            "signal_id": signal.signal_id,
            "variant_results": results,
        }

    def apply_signal_from_quotes(
        self,
        *,
        event_id: str,
        signal: Signal,
        zone: ZoneDecision,
        quotes: list[ExecutionQuote],
    ) -> dict[str, Any]:
        quote = next_available_quote(
            quotes, strictly_after_ms=signal.decision_at_ms
        )
        if quote is None:
            return self.apply_signal(
                event_id=event_id,
                signal=signal,
                zone=zone,
                execution_price=None,
                execution_quantity=None,
                execution_timestamp_ms=None,
            )
        price, quantity = executable_side(
            quote, position_side=signal.side, action="entry"
        )
        return self.apply_signal(
            event_id=event_id,
            signal=signal,
            zone=zone,
            execution_price=price,
            execution_quantity=quantity,
            execution_timestamp_ms=quote.timestamp_ms,
        )

    def process_bar(self, bar: ReplayBar) -> list[ExitIntent]:
        intents = []
        for portfolio in self.portfolios.values():
            intents.extend(portfolio.process_bar(bar))
        return intents

    def execute_pending_exits(
        self, quotes: list[ExecutionQuote]
    ) -> dict[str, list[dict[str, Any]]]:
        results: dict[str, list[dict[str, Any]]] = {}
        for name, portfolio in self.portfolios.items():
            rows = []
            for position_id in list(portfolio.positions):
                position = portfolio.positions[position_id]
                if position.pending_exit is not None:
                    rows.append(
                        portfolio.execute_pending_exit(position_id, quotes)
                    )
            results[name] = rows
        return results

    def apply_bar_event(
        self,
        *,
        event_id: str,
        bar: ReplayBar,
        quotes: list[ExecutionQuote],
    ) -> dict[str, Any]:
        if event_id in self.seen_event_ids:
            return {"duplicate": True, "event_id": event_id}
        self.seen_event_ids.add(event_id)
        intents = self.process_bar(bar)
        executions = self.execute_pending_exits(quotes)
        return {
            "event_id": event_id,
            "exit_intents": [asdict(intent) for intent in intents],
            "executions": executions,
        }

    def apply_funding_event(
        self,
        *,
        event_id: str,
        variant: str,
        position_id: str,
        cash_amount: float | None,
        ts_ms: int,
    ) -> dict[str, Any]:
        if event_id in self.seen_event_ids:
            return {"duplicate": True, "event_id": event_id}
        if variant not in self.portfolios:
            raise ValueError(f"unknown variant {variant}")
        result = self.portfolios[variant].apply_funding_event(
            position_id,
            event_id=event_id,
            cash_amount=cash_amount,
            ts_ms=ts_ms,
        )
        if result.get("applied") or result.get("unknown"):
            self.seen_event_ids.add(event_id)
        return result

    def apply_funding_coverage(
        self,
        *,
        variant: str,
        position_id: str,
        through_ms: int,
        cumulative_cash_amount: float,
        source_verified: bool,
    ) -> dict[str, Any]:
        if variant not in self.portfolios:
            raise ValueError(f"unknown variant {variant}")
        return self.portfolios[variant].set_funding_coverage(
            position_id,
            through_ms=through_ms,
            cumulative_cash_amount=cumulative_cash_amount,
            source_verified=source_verified,
        )

    def mark_to_market_event(
        self,
        *,
        event_id: str,
        marks: dict[str, float],
        timestamp_ms: int,
    ) -> dict[str, Any]:
        if event_id in self.seen_event_ids:
            return {"duplicate": True, "event_id": event_id}
        self.seen_event_ids.add(event_id)
        return {
            name: portfolio.mark_to_market(marks, timestamp_ms=timestamp_ms)
            for name, portfolio in self.portfolios.items()
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
        )
        if not result.get("unknown"):
            self.seen_event_ids.add(event_id)
        return result

    def export_state(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "seen_event_ids": sorted(self.seen_event_ids),
            "portfolios": {
                name: portfolio.export_state()
                for name, portfolio in self.portfolios.items()
            },
        }

    @classmethod
    def import_state(cls, state: dict[str, Any]) -> ShadowReplay:
        if state.get("schema_version") != 2:
            raise ValueError("unsupported replay state schema")
        portfolios = {
            name: VirtualPortfolio.import_state(row)
            for name, row in state["portfolios"].items()
        }
        if set(portfolios) != {"A", "B", "C"}:
            raise ValueError("replay state must contain A, B and C")
        return cls(portfolios, set(state.get("seen_event_ids", [])))
