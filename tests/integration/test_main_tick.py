"""Integration: full tick path without real BingX (mocked exchange)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from astra_bot.core import models
from astra_bot.decision.broker import PaperBroker
from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig
from astra_bot.main import AstraBot


def _candle(ts: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> models.Candle:
    return models.Candle(
        open_time=ts,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=Decimal(str(v)),
        close_time=ts + 299_000,
    )


def _relax_min_rr_for_fixture(eng) -> None:
    """Sprint min_rr=2.0 is production policy; weak candle fixtures need 0.5.

    Integration tests here assert the orchestration path (tick → risk →
    broker), not RR quality of synthetic bars.
    """
    cfg = getattr(eng, "config", None)
    if cfg is not None and hasattr(cfg, "min_rr"):
        cfg.min_rr = 0.5
    pipe = getattr(eng, "pipeline", None)
    if pipe is not None:
        dcfg = getattr(pipe, "config", None)
        if dcfg is not None and hasattr(dcfg, "min_rr"):
            dcfg.min_rr = 0.5
            if hasattr(dcfg, "min_ev_r"):
                dcfg.min_ev_r = 0.0


@pytest.fixture
def make_bot(tmp_path: Path):
    def _make(n_bars: int = 80):
        exchange = AsyncMock()
        bars = [
            _candle(1_700_000_000_000 + i * 300_000, 100 + i * 0.01, 101 + i * 0.01, 99 + i * 0.01, 100.5 + i * 0.01)
            for i in range(n_bars)
        ]
        exchange.get_candles = AsyncMock(return_value=bars)
        exchange.get_orderbook = AsyncMock(return_value=None)
        exchange.get_ticker = AsyncMock(return_value={"last": 100.5, "high_24h": 102, "low_24h": 98})
        exchange.get_account_balance = AsyncMock(return_value={})
        exchange.get_instrument = AsyncMock(return_value=None)

        te_cfg = TradingEngineConfig(
            symbols=("BTC-USDT",),
            state_path=str(tmp_path / "pos.json"),
            trades_path=str(tmp_path / "trades.jsonl"),
            stats_path=str(tmp_path / "stats.json"),
            no_trade_observations_path=str(tmp_path / "no_trade.jsonl"),
            no_trade_outcomes_path=str(tmp_path / "no_trade_out.json"),
            hypotheses_path=str(tmp_path / "hyp.json"),
            halt_alerts_path=str(tmp_path / "halt.json"),
            pattern_exit_shadow_path=str(tmp_path / "pattern_shadow.jsonl"),
            klines_cache_dir=str(tmp_path / "klines"),
        )
        bot = AstraBot.__new__(AstraBot)
        bot._exchange = exchange
        bot._trading_engine = TradingEngine(exchange=exchange, config=te_cfg)
        _relax_min_rr_for_fixture(bot._trading_engine)
        return bot

    return _make


@pytest.mark.asyncio
async def test_main_tick_runs_without_crash(make_bot):
    bot = make_bot()
    eng = bot._trading_engine
    await eng.step()
    assert eng.broker is not None


@pytest.mark.asyncio
async def test_main_tick_repeated_idempotent(make_bot):
    bot = make_bot()
    eng = bot._trading_engine
    await eng.step()
    await eng.step()
    assert eng.broker is not None


@pytest.mark.asyncio
async def test_process_symbol_handles_empty_pipeline(make_bot, tmp_path):
    bot = make_bot()
    eng = bot._trading_engine
    _relax_min_rr_for_fixture(eng)
    closed = await eng.process_symbol("BTC-USDT")
    assert isinstance(closed, list)
