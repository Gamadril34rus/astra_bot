"""Deterministic BSR features and three-snapshot event detector."""
from __future__ import annotations
import math
from collections.abc import Sequence

from .models import BookSnapshot, MarketTrade, Signal, SignalConfig


def _mid(book: BookSnapshot) -> float:
    return (book.bids[0][0] + book.asks[0][0]) / 2.0


def _depth(levels, mid: float, band_bps: float, is_bid: bool) -> float:
    band = band_bps / 10_000.0
    boundary = mid * (1.0 - band if is_bid else 1.0 + band)
    return sum(p * q for p, q in levels if (p >= boundary if is_bid else p <= boundary))


def book_metrics(book: BookSnapshot, depth_bps: float = 20.0) -> dict[str, float]:
    mid = _mid(book)
    bid = _depth(book.bids, mid, depth_bps, True)
    ask = _depth(book.asks, mid, depth_bps, False)
    total = bid + ask
    return {
        "mid": mid,
        "spread_bps": (book.asks[0][0] - book.bids[0][0]) / mid * 10_000.0,
        "bid_depth": bid, "ask_depth": ask,
        "book_imbalance": (bid - ask) / total if total else 0.0,
    }


def trade_flow(trades: Sequence[MarketTrade], start_ms: int, end_ms: int) -> dict[str, float]:
    """buyer_maker=True means buyer is passive, hence seller is aggressor."""
    rows = [t for t in trades if start_ms <= t.timestamp_ms <= end_ms]
    buy = sum(t.price * t.quantity for t in rows if not t.buyer_maker)
    sell = sum(t.price * t.quantity for t in rows if t.buyer_maker)
    total = buy + sell
    return {"buy_notional": buy, "sell_notional": sell, "total_notional": total,
            "imbalance": (buy - sell) / total if total else 0.0}


def _drawdown_recovery(before: float, trough: float, recovered: float) -> tuple[float, float]:
    if before <= 0:
        return 0.0, 0.0
    drawdown = max(0.0, (before - trough) / before)
    removed = before - trough
    recovery = max(0.0, (recovered - trough) / removed) if removed > 0 else 0.0
    return min(drawdown, 1.0), min(recovery, 1.0)


def evaluate_signal(*, before: BookSnapshot, sweep: BookSnapshot, recovery: BookSnapshot,
                    trades: Sequence[MarketTrade], fair_price: float, now_ms: int,
                    config: SignalConfig | None = None) -> Signal | None:
    """Evaluate pre-sweep -> sweep-extreme -> recovery; this function never trades."""
    config = config or SignalConfig()
    if fair_price <= 0:
        raise ValueError("fair_price must be positive and independently sourced")
    if not (before.symbol == sweep.symbol == recovery.symbol):
        raise ValueError("snapshots must refer to the same symbol")
    if not (before.timestamp_ms < sweep.timestamp_ms <= recovery.timestamp_ms <= now_ms):
        raise ValueError("snapshot timestamps must be strictly ordered")
    if now_ms - recovery.timestamp_ms > config.max_snapshot_age_ms:
        return None
    if recovery.timestamp_ms - sweep.timestamp_ms > config.max_snapshot_gap_ms:
        return None

    bm, sm, rm = (book_metrics(x, config.depth_bps) for x in (before, sweep, recovery))
    if not config.min_spread_bps <= rm["spread_bps"] <= config.max_spread_bps:
        return None
    mid = rm["mid"]
    deviation = math.log(mid / fair_price) * 10_000.0
    long_dd, long_rec = _drawdown_recovery(bm["bid_depth"], sm["bid_depth"], rm["bid_depth"])
    short_dd, short_rec = _drawdown_recovery(bm["ask_depth"], sm["ask_depth"], rm["ask_depth"])
    rebound_long = (mid / sm["mid"] - 1.0) * 10_000.0
    rebound_short = (sm["mid"] / mid - 1.0) * 10_000.0
    flow = trade_flow(trades, sweep.timestamp_ms, recovery.timestamp_ms)

    long_ok = (deviation <= -config.min_dislocation_bps
        and flow["imbalance"] <= -config.min_flow_imbalance
        and flow["sell_notional"] >= config.min_aggressive_notional
        and long_dd >= config.min_depth_drawdown
        and long_rec >= config.min_depth_recovery
        and rebound_long >= config.min_rebound_bps
        and rm["book_imbalance"] > 0)
    short_ok = (deviation >= config.min_dislocation_bps
        and flow["imbalance"] >= config.min_flow_imbalance
        and flow["buy_notional"] >= config.min_aggressive_notional
        and short_dd >= config.min_depth_drawdown
        and short_rec >= config.min_depth_recovery
        and rebound_short >= config.min_rebound_bps
        and rm["book_imbalance"] < 0)
    if not long_ok and not short_ok:
        return None

    if long_ok:
        direction, dd, rec, rebound = "LONG", long_dd, long_rec, rebound_long
        book_strength = max(rm["book_imbalance"], 0.0)
    else:
        direction, dd, rec, rebound = "SHORT", short_dd, short_rec, rebound_short
        book_strength = abs(min(rm["book_imbalance"], 0.0))
    score = 30.0 * min(abs(deviation) / 20.0, 1.0)
    score += 25.0 * min(abs(flow["imbalance"]) / 0.6, 1.0)
    score += 25.0 * min(dd / 0.5, 1.0)
    score += 20.0 * min(book_strength / 0.5, 1.0) * min(rec, 1.0)
    if score < config.min_score:
        return None
    return Signal(
        symbol=recovery.symbol, direction=direction, score=score,
        timestamp_ms=recovery.timestamp_ms, mid_price=mid, fair_price=fair_price,
        deviation_bps=deviation, spread_bps=rm["spread_bps"],
        book_imbalance=rm["book_imbalance"], trade_imbalance=flow["imbalance"],
        aggressive_notional=flow["total_notional"], depth_drawdown=dd,
        depth_recovery=rec, rebound_bps=rebound,
    )
