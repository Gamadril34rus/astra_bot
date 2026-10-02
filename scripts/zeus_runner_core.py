#!/usr/bin/env python3
"""Zeus paper-clock: channel capital + matched wedge + confidence leverage."""

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
