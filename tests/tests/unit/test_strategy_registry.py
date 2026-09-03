"""Тесты единого реестра стратегий и аудиторского контура."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from astra_bot.core import models
from astra_bot.decision.strategy_registry import (
    STRATEGY_REGISTRY,
    execution_strategies,
)
from astra_bot.strategies.multicurrency_mtf import (
    MulticurrencyMTFStrategy,
)


def _create_candles(n: int, tf: str = "1h", base_px: float = 100.0, step: float = 0.001) -> list[models.Candle]:
    candles = []
    px = base_px
    for i in range(n):
        op = px
        cl = px * (1 + step)
        candles.append(
            models.Candle(
                exchange="test",
                symbol="ETH/USDT",
                timeframe=tf,
                open_time=1600000000000 + i * 3600000,
                open=Decimal(str(round(op, 6))),
                high=Decimal(str(round(max(op, cl) * 1.002, 6))),
                low=Decimal(str(round(min(op, cl) * 0.998, 6))),
                close=Decimal(str(round(cl, 6))),
                volume=Decimal("100"),
                quote_volume=Decimal("10000"),
            )
        )
        px = cl
    return candles


def test_strategy_registry_contains_required_keys():
    required_keys = {
        "book_breakout",
        "momentum",
        "mean_reversion",
        "pullback",
        "high_winrate",
        "selective",
        "ts_momentum",
        "ts_momentum_adx",
        "multicurrency_mtf",
        "livermore_pivot",
        "soros_regime",
        "druckenmiller_driver",
        "tudor_risk",
    }
    assert required_keys.issubset(set(STRATEGY_REGISTRY.keys()))


def test_execution_strategies_fail_closed_by_default():
    # Без TIER_CHAMPION ни одна стратегия не разрешена к исполнению
    assert execution_strategies() == []


@pytest.mark.asyncio
async def test_multicurrency_mtf_blocks_when_missing_timeframes():
    s = MulticurrencyMTFStrategy()
    c = _create_candles(300)
    # Нет кредитных раздельных 1D/4H/1H свечей
    sig = await s.evaluate("ETH/USDT", c)
    assert sig is None


@pytest.mark.asyncio
async def test_multicurrency_mtf_btc_bearish_gate_blocks_altcoin_long():
    s = MulticurrencyMTFStrategy()

    c_1d_bull = _create_candles(300, tf="1d", step=0.005)
    c_4h = _create_candles(300, tf="4h", step=0.002)
    c_1h = _create_candles(300, tf="1h", step=0.001)

    # BTC в жестком 1D медвежьем тренде
    btc_1d_bear = _create_candles(300, tf="1d", step=-0.005)

    sig = await s.evaluate(
        symbol="ETH/USDT",
        candles=c_1h,
        candles_1d=c_1d_bull,
        candles_4h=c_4h,
        candles_1h=c_1h,
        btc_candles_1d=btc_1d_bear,
    )
    assert sig is None


def test_audit_cli_generates_json_report(tmp_path):
    from scripts.audit_brains import main as audit_main

    # Для теста генерируем минимальный синтетический CSV в фиксированном
    # месте, откуда скрипт ожидает данные (не используем сеть).
    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    data_csv = data_dir / "BTCUSDT_4h.csv"
    if not data_csv.exists():
        import numpy as np
        import pandas as pd
        idx = pd.date_range("2021-01-01", "2024-12-31", freq="4h")
        n = len(idx)
        rng = np.random.default_rng(42)
        price = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.005, n)))
        df = pd.DataFrame({
            "open_time": [int(t.timestamp() * 1000) for t in idx],
            "open": price,
            "high": price * 1.002,
            "low": price * 0.998,
            "close": price * (1 + rng.normal(0, 0.001, n)),
            "volume": rng.uniform(100, 1000, n),
            "close_time": [int(t.timestamp() * 1000) + 4*3600*1000-1 for t in idx],
            "quote_volume": rng.uniform(1e5, 1e6, n),
            "count": rng.integers(100, 1000, n),
            "taker_buy_volume": rng.uniform(50, 500, n),
            "taker_buy_quote_volume": rng.uniform(5e4, 5e5, n),
            "ignore": 0,
        })
        df.to_csv(data_csv, index=False)

    out_json = tmp_path / "brain_audit.json"
    sys_argv = [
        "audit_brains.py",
        "--data",
        "data/BTCUSDT_4h.csv",
        "--symbol",
        "BTC/USDT",
        "--timeframe",
        "4h",
        "--start",
        "2021-01-01",
        "--oos-start",
        "2024-01-01",
        "--end",
        "2024-12-31",
        "--out",
        str(out_json),
    ]

    import sys
    orig_argv = sys.argv
    try:
        sys.argv = sys_argv
        code = audit_main()
        assert code == 0
        assert out_json.exists()
        data = json.loads(out_json.read_text(encoding="utf-8"))
        assert "metadata" in data
        assert "strategies" in data
        assert data["strategies"]["livermore_pivot"]["status"] == "not_auditable"
        assert data["strategies"]["ts_momentum"]["status"] == "audited"
    finally:
        sys.argv = orig_argv
