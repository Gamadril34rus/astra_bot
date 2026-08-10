"""
ASTRA BOT — Загрузка годичной истории и обучение без депозита.

Сценарий использования:

1. Подтягиваем ~1 год свечей с OKX (с пагинацией по ``before``).
2. На исторических данных прогоняем стратегии (paper-бэктест) и собираем
   пары (признаки на момент входа, исход сделки).
3. Обучаем ML-модель на полученном датасете.
4. Сохраняем артефакт в ``models/`` и регистрируем в ModelRegistry.

Реальные деньги на этом этапе не используются — это бэктест + обучение.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..adapters.okx.client import OKXClient
from ..core import models
from ..core.utils import calculate_timeframe_minutes
from .feature_pipeline import FeaturePipeline, get_feature_pipeline
from .model_registry import ModelRegistry
from .model_trainer import ModelTrainer, TrainingConfig, TrainingData

logger = logging.getLogger(__name__)

# OKX отдаёт максимум 100 свечей за запрос на endpoint /history-candles
OKX_MAX_CANDLES_PER_REQUEST = 100


@dataclass
class HistoricalTrainingConfig:
    """Параметры загрузки истории и обучения."""

    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    lookback_days: int = 365
    model_type: str = "lightgbm"
    initial_capital: Decimal = Decimal("10000")
    min_samples: int = 200
    output_dir: str = "models"
    # OKX использует формат "BTC-USDT" вместо "BTC/USDT".
    okx_symbol: str | None = None

    @property
    def exchange_symbol(self) -> str:
        if self.okx_symbol:
            return self.okx_symbol
        return self.symbol.replace("/", "-")


async def fetch_historical_candles(
    client: OKXClient,
    symbol: str,
    timeframe: str,
    lookback_days: int,
    sleep_between_requests: float = 0.15,
) -> list[models.Candle]:
    """Загрузить исторические свечи OKX за ``lookback_days`` дней.

    OKX endpoint ``/market/history-candles`` возвращает данные страницами
    по 100 свечей и поддерживает курсор ``before`` (timestamp мс): вернёт
    свечи *старше* переданного значения. Идём от текущего момента назад,
    пока не наберём запрошенный период.
    """
    minutes = calculate_timeframe_minutes(timeframe)
    total_candles = int(lookback_days * 24 * 60 / minutes)
    logger.info(
        "Загружаю %d свечей %s %s за %d дней",
        total_candles,
        symbol,
        timeframe,
        lookback_days,
    )

    collected: dict[int, models.Candle] = {}
    before: int | None = None

    while len(collected) < total_candles:
        batch = await client.get_candles(
            symbol=symbol,
            timeframe=timeframe,
            since=before,
            limit=OKX_MAX_CANDLES_PER_REQUEST,
        )
        if not batch:
            logger.warning("OKX не вернул свечей на курсоре before=%s", before)
            break

        for candle in batch:
            collected[candle.open_time] = candle

        # Следующая страница — старше самой ранней свечи.
        oldest = min(c.open_time for c in batch)
        if oldest == before:
            # Нет более старых данных.
            break
        before = oldest

        if len(batch) < OKX_MAX_CANDLES_PER_REQUEST:
            # Достигли начала доступной истории.
            break

        # Гость не без лимитов — спим между запросами.
        time.sleep(sleep_between_requests)

    candles = sorted(collected.values(), key=lambda c: c.open_time)
    cutoff = datetime.now(tz=UTC) - timedelta(days=lookback_days)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    candles = [c for c in candles if c.open_time >= cutoff_ms]

    logger.info("Загружено %d свечей", len(candles))
    return candles


def _walk_forward_labels(
    candles: list[models.Candle],
    forward_periods: int = 4,
    take_profit_pct: float = 0.015,
    stop_loss_pct: float = 0.01,
) -> list[dict[str, Any]]:
    """Сформировать учебные метки без использования реального депозита.

    Для каждой свечи (кроме последних ``forward_periods``) смотрим на
    движение цены в следующих N барах: если максимум достиг TP раньше, чем
    минимум — SL, метка 1 (прибыльная сделка), иначе 0.

    Это имитирует реальные выходы стратегии и даёт модели обучаться на
    годе рыночных данных без совершения сделок и без пополнения счёта.
    """
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
                # В один и тот же бар — консервативно считаем убыточным.
                won = False
                break
            if highs[j] >= tp:
                won = True
                break
            if lows[j] <= sl:
                won = False
                break

        labels.append(
            {
                "timestamp": candles[i].open_time,
                "symbol": candles[i].symbol,
                "entry": entry,
                "target": 1 if won else 0,
            }
        )

    return labels


def build_training_dataset(
    candles: list[models.Candle],
    feature_pipeline: FeaturePipeline | None = None,
) -> TrainingData:
    """Превратить историю свечей в TrainingData для MLModel."""
    if len(candles) < 50:
        raise ValueError("Недостаточно свечей для построения признаков")

    pipeline = feature_pipeline or get_feature_pipeline()
    labels = _walk_forward_labels(candles)

    feature_names = pipeline.feature_names
    rows: list[list[float]] = []
    targets: list[int] = []

    for label in labels:
        # Берём все свечи ДО момента сделки.
        idx = next(
            (
                i
                for i, c in enumerate(candles)
                if c.open_time >= label["timestamp"]
            ),
            None,
        )
        if idx is None or idx < 50:
            continue

        feature_vector = pipeline.generate_features(
            symbol=label["symbol"],
            candles=candles[: idx + 1],
        )
        if not feature_vector.is_valid:
            continue

        rows.append(
            [feature_vector.features.get(name, 0.0) for name in feature_names]
        )
        targets.append(label["target"])

    if not rows:
        raise ValueError("Не удалось собрать ни одного обучающего примера")

    import numpy as np

    X = np.array(rows, dtype=float)
    y = np.array(targets, dtype=int)
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
    """Полный pipeline: история → датасет → обучение → сохранение."""
    cfg = config or HistoricalTrainingConfig()
    close_client = False
    if client is None:
        client = OKXClient(
            {
                "api_key": "",
                "api_secret": "",
                "sandbox": False,
                "enabled": True,
                # Публичные эндпоинты, уважаем рейт-лимит.
                "rate_limit_qps": 5,
            }
        )
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

    if len(candles) < 100:
        raise RuntimeError(
            f"Получено слишком мало свечей: {len(candles)}"
        )

    dataset = build_training_dataset(candles)
    if dataset.n_samples < cfg.min_samples:
        raise RuntimeError(
            f"Собрано {dataset.n_samples} примеров, "
            f"нужно минимум {cfg.min_samples}"
        )

    logger.info(
        "Обучаю модель %s на %d примерах (positive rate %.2f)",
        cfg.model_type,
        dataset.n_samples,
        float(dataset.labels.mean()),
    )

    training_config = TrainingConfig(model_type=cfg.model_type)
    trainer_instance = trainer or ModelTrainer(training_config)
    model = trainer_instance.train(dataset, model_type=cfg.model_type)

    output_dir = Path(cfg.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    version = datetime.now(tz=UTC).strftime("ML-%Y%m%d-%H%M%S")
    artifact = output_dir / f"{version}.pkl"
    model.save(str(artifact))

    if registry is not None:
        try:
            registry.register(
                model=model,
                version=version,
                description=f"Trained on {cfg.lookback_days}d of {cfg.symbol} {cfg.timeframe}",
                tags=["historical", cfg.symbol, cfg.timeframe],
            )
        except Exception as exc:  # pragma: no cover - registry опционален
            logger.warning("Не удалось зарегистрировать модель: %s", exc)

    logger.info("Модель сохранена: %s", artifact)
    return artifact
