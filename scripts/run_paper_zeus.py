#!/usr/bin/env python3
"""Zeus paper-clock: wedge + channel (lessons), multi-symbol, paper-only."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from astra_bot.adapters.bingx import BingXClient
from astra_bot.core.logger import setup_logging
from astra_bot.decision.config import DecisionConfig
from astra_bot.decision.pipeline import DecisionPipeline
from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig
from astra_bot.decision.zeus_trade_log import ZeusTradeLog
from astra_bot.strategies.zeus_channel_boundary import (
    ZeusChannelBoundaryConfig,
    ZeusChannelBoundaryStrategy,
)
from astra_bot.strategies.zeus_wedge_retest import (
    ZeusWedgeRetestConfig,
    ZeusWedgeRetestStrategy,
)

logger = logging.getLogger("paper_zeus")

LTF_IMPULSE_RANGE_MULT = 1.8
LTF_IMPULSE_VOL_MULT = 1.6
LTF_LOOKBACK = 24

ZEUS_STATE_PATH = "models/zeus_paper_positions.json"
ZEUS_TRADES_PATH = "models/zeus_paper_trades.jsonl"
ZEUS_STATS_PATH = "models/zeus_strategy_stats.json"
ZEUS_NO_TRADE_OBS = "models/zeus_no_trade_observations.jsonl"
ZEUS_NO_TRADE_OUT = "models/zeus_no_trade_outcomes.json"
ZEUS_PATTERN_EXIT = "models/zeus_pattern_exit_shadow.jsonl"
ZEUS_HALT_ALERTS = "models/zeus_halt_alerts.json"
ZEUS_HYPOTHESES = "models/zeus_hypotheses.json"
ZEUS_KLINES_CACHE = "models/zeus_klines_cache"

DEFAULT_SYMBOLS = (
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "BNB-USDT",
    "XRP-USDT",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Zeus paper multi-symbol")
    p.add_argument("--symbol", default="")
    p.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    p.add_argument("--interval", type=int, default=300)
    p.add_argument("--capital", type=float, default=2000.0)
    p.add_argument("--once", action="store_true")
    p.add_argument("--journal", default="models/zeus_trade_journal.jsonl")
    return p.parse_args()


def resolve_symbols(args: argparse.Namespace) -> tuple[str, ...]:
    if (args.symbol or "").strip():
        return (args.symbol.strip().upper(),)
    parts = [s.strip().upper() for s in (args.symbols or "").split(",") if s.strip()]
    return tuple(parts) if parts else DEFAULT_SYMBOLS


def _snap_positions(engine: TradingEngine) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    try:
        for p in engine.broker.positions or []:
            tps = getattr(p, "take_profits", None) or []
            tp0 = ""
            if tps:
                first = tps[0]
                tp0 = (
                    str(first)
                    if not isinstance(first, dict)
                    else str(first.get("price", ""))
                )
            out[str(p.id)] = {
                "stop_loss": str(p.stop_loss),
                "entry_price": str(p.entry_price),
                "direction": str(getattr(p, "direction", "") or ""),
                "quantity": str(p.quantity),
                "symbol": getattr(p, "symbol", "") or "",
                "strategy": getattr(p, "strategy", "") or "",
                "take_profit": tp0,
            }
    except Exception as exc:
        logger.debug("snap: %s", exc)
    return out


def _load_known_trade_ids(trades_path: Path) -> set[str]:
    known: set[str] = set()
    if not trades_path.exists():
        return known
    try:
        import json as _json

        for line in trades_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = _json.loads(line)
            tid = str(row.get("id") or row.get("trade_id") or "")
            if tid:
                known.add(tid)
    except Exception as exc:
        logger.debug("known ids: %s", exp)
    return known
