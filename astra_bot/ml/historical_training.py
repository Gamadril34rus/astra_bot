"""
ASTRA BOT — Историческое обучение без депозита.

Загружает доступную историю OKX, проводит walk-forward paper trading,
складывает сделки-уроки и обучает ML-модель. Реальные деньги не используются.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..adapters.okx.client import OKXClient, OKX_ENDPOINTS
from ..core import models
from ..core.utils import calculate_timeframe_minutes
from .feature_pipeline import FeaturePipeline, get_feature_pipeline
from .model_registry import ModelRegistry
from .model_trainer import ModelTrainer, TrainingConfig, TrainingData

logger = logging.getLogger(__name__)

# Реальный лимит OKX для history-candles/pagination.
OKX_MAX_CANDLES_PER_REQUEST = 100


@dataclass
class HistoricalTrainingConfig:
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    lookback_days: int = 365
    model_type: str = "lightgbm"
    initial_capital: Decimal = Decimal("10000")
    min_samples: int = 200
    output_dir: str = "models"
    okx_symbol: str | None = None

    @property
    def exchange_symbol(self) -> str:
        return self.okx_symbol or self.symbol.replace("/", "-")


async def fetch_historical_candles(
    client: OKXClient,
    symbol: str,
    timeframe: str,
    lookback_days: int,
    sleep_between_requests: float = 0.05,
) -> list[models.Candle]:
    """Загрузить историю, двигаясь от новых свечей к старым через ``after``."""
    minutes = calculate_timeframe_minutes(timeframe)
    total_candles = int(lookback_days * 24 * 60 / minutes)
    logger.info(
        "Загружаю %d свечей %s %s за %d дней",
        total_candles, symbol, timeframe, lookback_days,
    )

    collected: dict[int, models.Candle] = {}
    after: int | None = None
    seen_cursors: set[int] = set()

    while len(collected) < total_candles:
        params = {
            "instId": symbol.replace("/", "-"),
            "bar": client._convert_timeframe(timeframe),
            "limit": OKX_MAX_CANDLES_PER_REQUEST,
        }
        if after is not None:
            params["after"] = after

        data = await client._request(
            "GET", OKX_ENDPOINTS["spot"]["candles"], params=params, signed=False
        )
        if not data:
            break

        oldest: int | None = None
        for item in data:
            candle = models.Candle(
                exchange="okx",
                symbol=symbol.replace("/", "-"),
                timeframe=timeframe,
                open_time=int(item[0]),
                open=Decimal(item[1]),
                high=Decimal(item[2]),
                low=Decimal(item[3]),
                close=Decimal(item[4]),
                volume=Decimal(item[5]),
                quote_volume=Decimal(item[6]) if len(item) > 6 else Decimal("0"),
                trades_count=0,
            )
            collected[candle.open_time] = candle
            oldest = candle.open_time if oldest is None else min(oldest, candle.open_time)

        if oldest is None or oldest in seen_cursors or oldest == after:
            logger.warning("Пагинация OKX остановлена на cursor=%s", after)
            break
        seen_cursors.add(oldest)
        after = oldest

        if len(data) < OKX_MAX_CANDLES_PER_REQUEST:
            break
        if sleep_between_requests > 0:
            await asyncio.sleep(sleep_between_requests)

    candles = sorted(collected.values(), key=lambda c: c.open_time)
    cutoff_ms = int(
        (datetime.now(tz=UTC) - timedelta(days=lookback_days)).timestamp() * 1000
    )
    candles = [c for c in candles if c.open_time >= cutoff_ms]
    logger.info("Загружено %d свечей %s %s", len(candles), symbol, timeframe)
    return candles


def _walk_forward_labels(
    candles: list[models.Candle],
    forward_periods: int = 4,
    take_profit_pct: float = 0.015,
    stop_loss_pct: float = 0.01,
) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    closes = [float(c.close) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]

    for i in range(50, len(candles) - forward_periods):
        entry = closes[i]
        tp = entry * (1 + take_profit_pct)
        sl = entry * (1 - stop_loss_pct)
        won = False
        for j in range(i + 1, min(i + 1 + forward_periods, len(candles))):
            if highs[j] >= tp and lows[j] <= sl:
                won = False
                break
            if highs[j] >= tp:
                won = True
                break
            if lows[j] <= sl:
                won = False
                break
        labels.append({
            "timestamp": candles[i].open_time,
            "symbol": candles[i].symbol,
            "entry": entry,
            "target": 1 if won else 0,
        })
    return labels


def build_training_dataset(
    candles: list[models.Candle],
    feature_pipeline: FeaturePipeline | None = None,
) -> TrainingData:
    if len(candles) < 200:
        raise ValueError("Недостаточно свечей для построения признаков")

    pipeline = feature_pipeline or get_feature_pipeline()
    labels = _walk_forward_labels(candles)
    feature_names = pipeline.feature_names
    rows: list[list[float]] = []
    targets: list[int] = []

    for label in labels:
        idx = next(
            (i for i, c in enumerate(candles) if c.open_time >= label["timestamp"]),
            None,
        )
        if idx is None or idx < 200:
            continue
        fv = pipeline.generate_features(symbol=label["symbol"], candles=candles[: idx + 1])
        if not fv.is_valid:
            continue
        rows.append([fv.features.get(name, 0.0) for name in feature_names])
        targets.append(label["target"])

    if not rows:
        raise ValueError("Не удалось собрать обучающие примеры")

    import numpy as np

    X = np.asarray(rows, dtype=float)
    y = np.asarray(targets, dtype=int)
    return TrainingData(
        features=X,
        labels=y,
        feature_names=feature_names,
        metadata={
            "n_samples": len(rows),
            "positive_rate": float(y.mean()),
            "source": "historical_walk_forward",
            "candles": len(candles),
        },
    )


async def train_on_historical_data(
    config: HistoricalTrainingConfig | None = None,
    client: OKXClient | None = None,
    trainer: ModelTrainer | None = None,
    registry: ModelRegistry | None = None,
) -> Path:
    cfg = config or HistoricalTrainingConfig()
    close_client = False
    if client is None:
        client = OKXClient({
            "api_key": "", "api_secret": "", "sandbox": False,
            "enabled": True, "rate_limit_qps": 8,
        })
        await client.initialize()
        close_client = True

    try:
        candles = await fetch_historical_candles(
            client=client,
            symbol=cfg.exchange_symbol,
            timeframe=cfg.timeframe,
            lookback_days=cfg.lookback_days,
        )
    finally:
        if close_client:
            await client.close()

    if len(candles) < 200:
        raise RuntimeError(f"Получено слишком мало свечей: {len(candles)}")

    dataset = build_training_dataset(candles)
    if dataset.n_samples < cfg.min_samples:
        raise RuntimeError(
            f"Собрано {dataset.n_samples} примеров, нужно минимум {cfg.min_samples}"
        )

    trainer_instance = trainer or ModelTrainer(TrainingConfig(model_type=cfg.model_type))
    model = trainer_instance.train(dataset, model_type=cfg.model_type)
    output_dir = Path(cfg.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    version = datetime.now(tz=UTC).strftime("ML-%Y%m%d-%H%M%S")
    artifact = output_dir / f"{version}.pkl"
    model.save(str(artifact), version=version)

    if registry is not None:
        try:
            registry.register(
                model=model,
                version=version,
                description=f"Trained on {cfg.lookback_days}d of {cfg.symbol} {cfg.timeframe}",
                tags=["historical", cfg.symbol, cfg.timeframe],
            )
        except Exception as exc:
            logger.warning("Model registry error: %s", exc)
    return artifact
