"""Arm feed producer: same ZeusWedgeRetest detector as paper → pending jsonl.

Writes models/zeus_arm_pending.jsonl only. No orders. No new thresholds.
≤1 candidate per symbol per bar. Freshness ts set on write.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from astra_bot.decision.live_arm_wiring import PENDING_PATH
from astra_bot.strategies.zeus_wedge_retest import (
    ZeusWedgeRetestConfig,
    ZeusWedgeRetestStrategy,
)

logger = logging.getLogger(__name__)

TIMEFRAME = "4h"
CANDLE_LIMIT = 120  # paper lookback=36 + detector tail headroom
STRATEGY_NAME = "zeus_wedge_retest"
# Same core set as paper DEFAULT_SYMBOLS (zeus_runner_common) — no new params.
FEED_SYMBOLS = (
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "BNB-USDT",
    "XRP-USDT",
)


def _paper_strategy() -> ZeusWedgeRetestStrategy:
    """Build the detector with the active paper runner's effective config.

    These values mirror scripts/zeus_research_wrap.py::research_config; the
    strategy defaults are disabled and are not suitable for the live feed.
    """
    return ZeusWedgeRetestStrategy(
        ZeusWedgeRetestConfig(
            enabled=True,
            lookback=36,
            min_touches=3,
            max_width_pct=0.06,
            min_width_pct=0.008,
            breakout_buffer_pct=0.001,
            retest_tolerance_pct=0.003,
            stop_buffer_pct=0.002,
            min_rr=3.5,
            max_bars_outside=5,
            enable_true_breakout=True,
            min_hold_bars_breakout=4,
            max_bars_true_breakout=6,
            min_stop_pct=0.008,
            require_htf_bias=True,
            tp1_rr=2.0,
            tp2_rr=3.5,
            tp1_fraction=0.4,
            tp2_fraction=0.6,
        )
    )


def make_signal_id(symbol: str, side: str, bar_ts: int, strategy: str = STRATEGY_NAME) -> str:
    """Stable id: strategy|symbol|side|bar_ts (one per symbol-bar)."""
    return f"{strategy}|{symbol}|{side.lower()}|{int(bar_ts)}"


def _direction_str(sig: Any) -> str:
    d = getattr(sig, "direction", None)
    if d is None:
        return ""
    v = getattr(d, "value", None)
    if v is not None:
        return str(v).lower()
    s = str(d).lower()
    if "long" in s:
        return "long"
    if "short" in s:
        return "short"
    return s


def signal_to_row(sig: Any, *, bar_ts: int, now: float | None = None) -> dict[str, Any]:
    now = now if now is not None else time.time()
    side = _direction_str(sig)
    if side not in ("long", "short"):
        raise ValueError(f"bad side {side!r}")
    entry = float(sig.entry_price)
    stop = float(sig.stop_loss)
    tp_raw = getattr(sig, "take_profit", None)
    tp = float(tp_raw) if tp_raw is not None else None
    conf = float(getattr(sig, "confidence", 0) or 0)
    symbol = str(sig.symbol)
    return {
        "signal_id": make_signal_id(symbol, side, bar_ts),
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "stop": stop,
        "tp": tp,
        "retest_ts": int(bar_ts),
        "ts": now,
        "strategy": STRATEGY_NAME,
        "confidence": conf,
    }


def write_pending(rows: list[dict[str, Any]], path: Path = PENDING_PATH) -> None:
    """Rewrite pending file (tick-scoped candidates only)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


async def fetch_candles(client: Any, symbol: str, *, limit: int = CANDLE_LIMIT) -> list:
    return await client.get_candles(symbol, TIMEFRAME, limit=limit)


async def scan_symbol(
    client: Any,
    symbol: str,
    strategy: ZeusWedgeRetestStrategy | None = None,
) -> dict[str, Any] | None:
    """≤1 candidate for symbol on latest closed-ish bar if detector fires."""
    strat = strategy or _paper_strategy()
    try:
        candles = await fetch_candles(client, symbol)
    except Exception as exc:
        logger.warning("arm feed: %s candles failed: %s", symbol, type(exc).__name__)
        return None
    if not candles or len(candles) < strat.config.lookback + 3:
        return None
    last = candles[-1]
    bar_ts = int(getattr(last, "open_time", 0) or 0)
    try:
        sig = await strat.evaluate(symbol, candles)
    except Exception as exc:
        logger.warning("arm feed: %s evaluate failed: %s", symbol, type(exc).__name__)
        return None
    if sig is None:
        return None
    try:
        return signal_to_row(sig, bar_ts=bar_ts)
    except Exception as exc:
        logger.warning("arm feed: %s row map failed: %s", symbol, type(exc).__name__)
        return None


async def produce_pending(
    client: Any,
    symbols: list[str] | tuple[str, ...],
    *,
    path: Path = PENDING_PATH,
    seen_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Scan symbols, write pending jsonl, return rows written.

    Dedup: skip signal_id already in seen_ids (entries journal) or duplicates
    within this tick (≤1 per symbol).
    """
    strat = _paper_strategy()
    seen = set(seen_ids or ())
    out: list[dict[str, Any]] = []
    for sym in symbols:
        row = await scan_symbol(client, sym, strategy=strat)
        if row is None:
            continue
        sid = row["signal_id"]
        if sid in seen:
            logger.info("arm feed: skip %s — signal_id already seen", sid)
            continue
        # ≤1 per symbol this tick
        if any(r["symbol"] == row["symbol"] for r in out):
            continue
        seen.add(sid)
        out.append(row)
    write_pending(out, path)
    logger.info("arm feed: wrote %d candidate(s) → %s", len(out), path)
    return out
