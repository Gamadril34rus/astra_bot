"""Тесты построения учебного датасета из годичной истории."""

import random
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from astra_bot.core import models
from astra_bot.ml.historical_training import (
    HistoricalTrainingConfig,
    _walk_forward_labels,
    build_training_dataset,
)


def _make_candles(n: int = 400, seed: int = 7) -> list[models.Candle]:
    random.seed(seed)
    candles = []
    base = 50000.0
    start = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    for i in range(n):
        base *= 1 + random.uniform(-0.005, 0.006)
        candles.append(
            models.Candle(
                exchange="okx",
                symbol="BTC-USDT",
                timeframe="1h",
                open_time=start + i * 3_600_000,
                open=Decimal(str(base * 0.999)),
                high=Decimal(str(base * 1.002)),
                low=Decimal(str(base * 0.998)),
                close=Decimal(str(base)),
                volume=Decimal(str(random.uniform(5, 20))),
                quote_volume=Decimal("500000"),
            )
        )
    return candles


def test_walk_forward_labels_returns_binary_targets():
    candles = _make_candles(200)
    labels = _walk_forward_labels(candles, forward_periods=4)
    assert len(labels) > 0
    assert all(label["target"] in (0, 1) for label in labels)
    assert all("timestamp" in label for label in labels)


def test_build_training_dataset_shape():
    candles = _make_candles(400)
    dataset = build_training_dataset(candles)
    assert dataset.n_samples > 50
    assert dataset.features.shape[0] == dataset.n_samples
    assert dataset.features.shape[1] == len(dataset.feature_names)
    assert set(dataset.labels.tolist()) <= {0, 1}


def test_build_training_dataset_validates_min_candles():
    with pytest.raises(ValueError):
        build_training_dataset(_make_candles(10))


def test_config_exchange_symbol_normalizes_slash_to_dash():
    cfg = HistoricalTrainingConfig(symbol="BTC/USDT")
    assert cfg.exchange_symbol == "BTC-USDT"
