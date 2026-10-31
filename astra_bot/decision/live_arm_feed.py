"""Arm feed: paper-parity channel detector -> models/zeus_arm_pending.jsonl.

Strategy = ZeusChannelBoundary with channel_capital_config() fields from
scripts/zeus_research_wrap.py (active paper runner). No new thresholds.
No orders - write pending only. ≤1 candidate per symbol per tick.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from astra_bot.decision.live_arm_wiring import PENDING_PATH
from astra_bot.strategies.zeus_channel_boundary import (
    ZeusChannelBoundaryConfig,
    ZeusChannelBoundaryStrategy,
)
from astra_bot.strategies.zeus_multi_top_breakdown import (
    detect_breakdown_from_ohlc,
    series_from_candles,
)

logger = logging.getLogger(__name__)

TIMEFRAME = "4h"
CANDLE_LIMIT = 120
STRATEGY_NAME = "zeus_channel_boundary"

# Universe v3 (owner 31.10 shadow): majors only until alts prove net+.
# Evidence: shadow 7d channel core +33%, alts drag; replay 48; hours thin/alts toxic.
# HYPE added per owner AUDIT 800.
FEED_SYMBOLS = (
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "HYPE-USDT",  # owner AUDIT 800
)

# asdict-diff vs ZeusChannelBoundaryConfig defaults (paper channel_capital_config):
#   enabled: False -> True
#   lookback: 30 -> 40
#   min_width_pct: 0.012 -> 0.01
#   min_rr: 1.5 -> 2.0
#   max_hold_bars: 8 -> 5
#   tp2_rr: 3.0 -> 2.0
# (min_stop_pct 0.008, require_htf_bias True, tp1_rr 1.5, fractions 0.4/0.6 - same)


def paper_channel_config() -> ZeusChannelBoundaryConfig:
    """Same fields as scripts/zeus_research_wrap.channel_capital_config()."""
    return ZeusChannelBoundaryConfig(
        enabled=True,
        lookback=40,
        min_width_pct=0.01,
        max_width_pct=0.12,
        min_rr=2.0,
        enable_breakout_hold=True,
        min_hold_bars=2,
        max_hold_bars=5,
        min_stop_pct=0.008,
        require_htf_bias=True,
        tp1_rr=1.5,
        tp2_rr=2.0,
        tp1_fraction=0.4,
        tp2_fraction=0.6,
    )


def make_signal_id(symbol: str, side: str, bar_ts: int, strategy: str = STRATEGY_NAME) -> str:
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


async def fetch_candles(
    client: Any,
    symbol: str,
    *,
    timeframe: str = TIMEFRAME,
    limit: int = CANDLE_LIMIT,
) -> list:
    return await client.get_candles(symbol, timeframe, limit=limit)


def _channel_strategy() -> ZeusChannelBoundaryStrategy:
    return ZeusChannelBoundaryStrategy(paper_channel_config())


async def scan_symbol(
    client: Any,
    symbol: str,
    strategy: ZeusChannelBoundaryStrategy | None = None,
) -> dict[str, Any] | None:
    strat = strategy or _channel_strategy()
    try:
        candles = await fetch_candles(client, symbol)
    except Exception as exc:
        logger.warning("arm feed: %s candles failed: %s", symbol, type(exc).__name__)
        return None
    if not candles or len(candles) < 30:
        return None
    # Live parity with paper: feed sees the SAME candle the paper engine uses
    # (including the forming bar). bar_ts = open_time of that last candle.
    series = list(candles)
    if len(series) < 30:
        return None
    last = series[-1]
    bar_ts = int(getattr(last, "open_time", 0) or 0)
    if bar_ts <= 0:
        logger.warning("arm feed: %s bar missing open_time - skip", symbol)
        return None
    try:
        sig = await strat.evaluate(symbol, series)
    except Exception as exc:
        logger.warning("arm feed: %s evaluate failed: %s", symbol, type(exc).__name__)
        sig = None
    if sig is not None:
        try:
            return signal_to_row(sig, bar_ts=bar_ts)
        except Exception as exc:
            logger.warning("arm feed: %s row map failed: %s", symbol, type(exc).__name__)
            return None
    # Owner 30.10: multi-top/bottom breakdown (missed BTC dump after 2-3 tops).
    # Prefer 4h structure; if silent, 1h fallback catches intra-4h continuations.
    br = None
    src_tf = "4h"
    try:
        highs, lows, closes = series_from_candles(series)
        br = detect_breakdown_from_ohlc(highs, lows, closes)
    except Exception as exc:
        logger.warning("arm feed: %s breakdown 4h failed: %s", symbol, type(exc).__name__)
    if br is None:
        try:
            c1h = await fetch_candles(client, symbol, timeframe="1h", limit=120)
            if c1h and len(c1h) >= 30:
                h1, l1, cl1 = series_from_candles(list(c1h))
                br = detect_breakdown_from_ohlc(h1, l1, cl1)
                if br is not None:
                    src_tf = "1h"
                    last1 = c1h[-1]
                    bar_ts = int(getattr(last1, "open_time", 0) or bar_ts)
        except Exception as exc:
            logger.warning("arm feed: %s breakdown 1h failed: %s", symbol, type(exc).__name__)
    if br is None:
        return None
    now = time.time()
    return {
        "signal_id": make_signal_id(symbol, br.side, bar_ts, strategy="zeus_multi_top_breakdown"),
        "symbol": symbol,
        "side": br.side,
        "entry": br.entry,
        "stop": br.stop,
        "tp": br.take_profit,
        "retest_ts": int(bar_ts),
        "ts": now,
        "strategy": "zeus_multi_top_breakdown",
        "confidence": br.confidence,
        "pattern": (br.meta or {}).get("pattern"),
        "neckline": br.neckline,
        "n_tops": br.n_tops,
        "tf": src_tf,
    }


async def produce_pending(
    client: Any,
    symbols: list[str] | tuple[str, ...] = FEED_SYMBOLS,
    *,
    path: Path = PENDING_PATH,
    seen_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    strat = _channel_strategy()
    seen = set(seen_ids or ())
    out: list[dict[str, Any]] = []
    open_syms: set[str] = set()
    try:
        from astra_bot.decision.live_positions import DEFAULT_PATH, load_positions
        for _pl in (load_positions(DEFAULT_PATH) or {}).values():
            if isinstance(_pl, dict) and _pl.get("symbol"):
                open_syms.add(str(_pl["symbol"]))
    except Exception:
        pass
    for sym in symbols:
        if sym in open_syms:
            logger.info("arm feed: skip %s - already open", sym)
            continue
        row = await scan_symbol(client, sym, strategy=strat)
        if row is None:
            continue
        sid = row["signal_id"]
        if sid in seen:
            logger.info("arm feed: skip %s - signal_id already seen", sid)
            continue
        if any(r["symbol"] == row["symbol"] for r in out):
            continue
        seen.add(sid)
        out.append(row)
    write_pending(out, path)
    try:
        jpath = Path("models/zeus_live_journal.jsonl")
        jpath.parent.mkdir(parents=True, exist_ok=True)
        with jpath.open("a", encoding="utf-8") as jf:
            for row in out:
                jf.write(
                    json.dumps(
                        {
                            "event": "pending_signal",
                            "signal_id": row.get("signal_id"),
                            "symbol": row.get("symbol"),
                            "side": row.get("side"),
                            "ts": time.time(),
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n"
                )
    except Exception as exc:
        logger.warning("arm feed journal: %s", type(exc).__name__)
    logger.info("arm feed: wrote %d channel candidate(s) -> %s", len(out), path)
    return out
