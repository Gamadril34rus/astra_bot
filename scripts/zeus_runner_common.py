"""Zeus paper-clock split 1/9: shared imports, constants, logger, engine config."""

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
ZEUS_SYMBOL_LOSS_GUARD = "models/zeus_symbol_loss_guard.json"
ZEUS_HYPOTHESES = "models/zeus_hypotheses.json"
ZEUS_KLINES_CACHE = "models/zeus_klines_cache"

DEFAULT_SYMBOLS = (
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "BNB-USDT",
    "XRP-USDT",
)

def zeus_engine_config(args: argparse.Namespace, symbols: tuple[str, ...]) -> TradingEngineConfig:
    config = TradingEngineConfig(
        symbols=symbols,
        poll_interval_seconds=args.interval,
        max_open_positions=5,
        structural_stop=True,
        smart_exit_default=True,
        state_path=ZEUS_STATE_PATH,
        trades_path=ZEUS_TRADES_PATH,
        stats_path=ZEUS_STATS_PATH,
        no_trade_observations_path=ZEUS_NO_TRADE_OBS,
        no_trade_outcomes_path=ZEUS_NO_TRADE_OUT,
        pattern_exit_shadow_path=ZEUS_PATTERN_EXIT,
        halt_alerts_path=ZEUS_HALT_ALERTS,
        symbol_loss_guard_path=ZEUS_SYMBOL_LOSS_GUARD,
        hypotheses_path=ZEUS_HYPOTHESES,
        klines_cache_dir=ZEUS_KLINES_CACHE,
        entry_gates_enabled=False,
        entry_gates_shadow_enabled=True,
        entry_gate_block_regimes=frozenset(),
    )
    return config
