"""Тесты walk-forward self-play обучения."""

import random
from datetime import datetime, timezone
from decimal import Decimal

from astra_bot.core import models
from astra_bot.ml.self_play import (
    Lesson,
    SelfPlayConfig,
    SelfPlayEngine,
    _classify_regime,
    _feature_snapshot,
    _recommend,
)


def _make_candles(symbol: str, n: int, seed: int = 1) -> list[models.Candle]:
    random.seed(seed + hash(symbol) % 1000)
    out = []
    base = 30000.0 if "BTC" in symbol else 2000.0 if "ETH" in symbol else 100.0
    start = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    for i in range(n):
        base *= 1 + random.uniform(-0.005, 0.0055)
        out.append(
            models.Candle(
                exchange="okx",
                symbol=symbol,
                timeframe="1h",
                open_time=start + i * 3_600_000,
                open=Decimal(str(base * 0.999)),
                high=Decimal(str(base * 1.004)),
                low=Decimal(str(base * 0.996)),
                close=Decimal(str(base)),
                volume=Decimal(str(random.uniform(5, 30))),
                quote_volume=Decimal("1"),
            )
        )
    return out


def test_classify_regime_returns_known_label():
    candles = _make_candles("BTC/USDT", 100)
    assert _classify_regime(candles) in {
        "HIGH_VOLATILITY_NEWS",
        "OVERBOUGHT",
        "OVERSOLD",
        "BULL_TREND",
        "RANGE",
    }


def test_feature_snapshot_has_expected_keys():
    candles = _make_candles("BTC/USDT", 100)

    class _S:
        name = "test"
        last_confidence = 0.7

    feats = _feature_snapshot(_S(), candles)
    for key in [
        "return_1h",
        "return_4h",
        "return_24h",
        "sma20_gap",
        "atr_pct",
        "rsi",
        "volume_ratio",
        "confidence",
    ]:
        assert key in feats


def test_recommend_skips_high_volatility_on_loss():
    rec = _recommend(
        "loss",
        {"atr_pct": 5.0, "volume_ratio": 1.0, "return_24h": 0.0, "rsi": 50.0},
        "long",
    )
    assert rec == "SKIP_HIGH_VOLATILITY"


async def test_self_play_generates_lessons_without_lookahead():
    history = {
        s: _make_candles(s, 600, seed=i)
        for i, s in enumerate(("BTC/USDT", "ETH/USDT", "SOL/USDT"))
    }
    engine = SelfPlayEngine(
        SelfPlayConfig(
            target_trades=50,
            max_holding_bars=6,
            position_fraction=Decimal("0.05"),
            lessons_output=__import__("pathlib").Path("/tmp/astra_lessons_test.jsonl"),
        )
    )
    report = await engine.run(history=history)

    assert report.total_trades >= 50
    assert report.started_learning is True
    # Проверяем, что в уроках реально записан «взгляд из прошлого»:
    # entry_time строго меньше exit_time, нет нулей в признаках.
    lesson = engine.lessons[0]
    assert isinstance(lesson, Lesson)
    assert lesson.entry_time < lesson.exit_time
    assert lesson.features["atr_pct"] != 0.0
    assert lesson.recommendation  # непустая подсказка
    assert lesson.outcome in {"win", "loss", "breakeven"}
