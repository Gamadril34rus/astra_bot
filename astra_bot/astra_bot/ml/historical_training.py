"""Historical BingX loader with conservative serialized throttling and bounded ranges.

Ретир OKX → BingX: историю (публичные kline) тянем с BingX spot,
лимит одной страницы — 1000 свечей, пагинация назад через endTime.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np

from ..adapters.bingx import BingXClient
from ..core import models
from ..core.utils import calculate_timeframe_minutes
from .feature_pipeline import FeaturePipeline, get_feature_pipeline
from .model_trainer import ModelTrainer, TrainingConfig, TrainingData

logger = logging.getLogger(__name__)

BINGX_MAX_CANDLES_PER_REQUEST = 1000
BINGX_MIN_REQUEST_INTERVAL = 0.9
BINGX_MAX_RETRIES = 6
BINGX_MAX_BACKOFF = 60.0


class RateLimiter:
    """Сериализованный троттлинг запросов к публичному kline API."""

    def __init__(self, min_interval: float = BINGX_MIN_REQUEST_INTERVAL) -> None:
        self.min_interval = max(min_interval, BINGX_MIN_REQUEST_INTERVAL)
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self.min_interval - (time.monotonic() - self._last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request = time.monotonic()


async def _fetch_page_with_retry(
    client: BingXClient,
    symbol: str,
    timeframe: str,
    end_time_ms: int | None,
    limiter: RateLimiter,
) -> list[models.Candle]:
    """Одна страница kline с retry на rate-limit/сетевые сбои."""
    last_error = None
    for attempt in range(BINGX_MAX_RETRIES):
        await limiter.wait()
        try:
            return await client.fetch_kline_page(
                symbol=symbol,
                timeframe=timeframe,
                end_time_ms=end_time_ms,
                limit=BINGX_MAX_CANDLES_PER_REQUEST,
            )
        except Exception as exc:
            last_error = exc
            text = str(exc)
            rate_limited = "rate" in text.lower() or "429" in text or "busy" in text.lower()
            if not rate_limited and attempt >= 2:
                raise
            delay = (
                min(BINGX_MAX_BACKOFF, 2.0 ** min(attempt + 1, 6))
                + random.uniform(0.2, 1.2)
            )
            logger.warning(
                "BingX request failed (%s), retry %d/%d in %.1fs",
                exc,
                attempt + 1,
                BINGX_MAX_RETRIES,
                delay,
            )
            await asyncio.sleep(delay)
    raise RuntimeError(f"BingX request failed after retries: {last_error}")


@dataclass
class HistoricalTrainingConfig:
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    lookback_days: int = 365
    model_type: str = "lightgbm"
    initial_capital: Decimal = Decimal("10000")
    min_samples: int = 200
    output_dir: str = "models"
    # Явный символ в формате BingX (BTC-USDT); по умолчанию — из symbol.
    bingx_symbol: str | None = field(default=None)

    @property
    def exchange_symbol(self) -> str:
        return self.bingx_symbol or self.symbol.replace("/", "-")


async def fetch_historical_candles(
    client: BingXClient,
    symbol: str,
    timeframe: str,
    lookback_days: int,
    sleep_between_requests: float = BINGX_MIN_REQUEST_INTERVAL,
    limiter: RateLimiter | None = None,
    end_time_ms: int | None = None,
) -> list[models.Candle]:
    """Fetch a finite historical window from BingX.

    ``end_time_ms`` lets pretrain process one calendar month at a time.
    Пагинация назад: каждая страница — свечи, закрытые до end_time_ms;
    шагаем на минус одну миллисекунду от самой старой свечи страницы.
    """
    minutes = calculate_timeframe_minutes(timeframe)
    total_candles = int(lookback_days * 24 * 60 / minutes)
    limiter = limiter or RateLimiter(
        max(sleep_between_requests, BINGX_MIN_REQUEST_INTERVAL)
    )
    collected: dict[int, models.Candle] = {}
    checkpoint = Path("artifacts/history_checkpoint.json")
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    key = f"{symbol}|{timeframe}|{lookback_days}|{end_time_ms or 'now'}"
    end_ms = end_time_ms or int(datetime.now(tz=UTC).timestamp() * 1000)
    after = end_ms
    while len(collected) < total_candles:
        bars = await _fetch_page_with_retry(client, symbol, timeframe, after, limiter)
        if not bars:
            break
        oldest = min(b.open_time for b in bars)
        for c in bars:
            collected[c.open_time] = c
        logger.info("history progress %s: %d/%d candles", key, len(collected), total_candles)
        checkpoint.write_text(
            json.dumps(
                {
                    "key": key,
                    "candles": len(collected),
                    "oldest": after,
                    "updated_at": time.time(),
                }
            ),
            encoding="utf-8",
        )
        if oldest >= after or len(bars) < BINGX_MAX_CANDLES_PER_REQUEST:
            break
        after = oldest - 1
    cutoff_ms = int(
        (
            datetime.fromtimestamp(end_ms / 1000, tz=UTC)
            - timedelta(days=lookback_days)
        ).timestamp()
        * 1000
    )
    result = [
        c
        for c in sorted(collected.values(), key=lambda c: c.open_time)
        if cutoff_ms <= c.open_time <= end_ms
    ]
    logger.info("history complete %s: %d candles", key, len(result))
    return result


def _walk_forward_labels(
    candles: list[models.Candle],
    forward_periods: int = 4,
    take_profit_pct: float = 0.015,
    stop_loss_pct: float = 0.01,
):
    labels = []
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
                break
            if highs[j] >= tp:
                won = True
                break
            if lows[j] <= sl:
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
    if len(candles) < 200:
        raise ValueError("Недостаточно свечей для построения признаков")
    pipeline = feature_pipeline or get_feature_pipeline()
    labels = _walk_forward_labels(candles)
    names = pipeline.feature_names
    rows: list[list[float]] = []
    targets: list[int] = []
    for label in labels:
        idx = next(
            (i for i, c in enumerate(candles) if c.open_time >= label["timestamp"]),
            None,
        )
        if idx is None or idx < 200:
            continue
        fv = pipeline.generate_features(
            symbol=label["symbol"], candles=candles[: idx + 1]
        )
        if not fv.is_valid:
            continue
        rows.append([fv.features.get(n, 0.0) for n in names])
        targets.append(label["target"])
    if not rows:
        raise ValueError("Не удалось собрать обучающие примеры")
    y = np.asarray(targets, dtype=int)
    return TrainingData(
        features=np.asarray(rows, dtype=float),
        labels=y,
        feature_names=names,
        metadata={
            "n_samples": len(rows),
            "positive_rate": float(y.mean()),
            "source": "historical_walk_forward",
            "candles": len(candles),
        },
    )


async def train_on_historical_data(
    config: HistoricalTrainingConfig | None = None,
    client: BingXClient | None = None,
    trainer=None,
    registry=None,
) -> Path:
    cfg = config or HistoricalTrainingConfig()
    close_client = False
    if client is None:
        # BingX: публичные kline не требуют ключей.
        client = BingXClient({"enabled": True, "rate_limit_qps": 1.2})
        await client.initialize()
        close_client = True
    try:
        candles = await fetch_historical_candles(
            client, cfg.exchange_symbol, cfg.timeframe, cfg.lookback_days
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
    model = (trainer or ModelTrainer(TrainingConfig(model_type=cfg.model_type))).train(
        dataset, model_type=cfg.model_type
    )
    output = Path(cfg.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    version = datetime.now(tz=UTC).strftime("ML-%Y%m%d-%H%M%S")
    artifact = output / f"{version}.pkl"
    model.save(str(artifact), version=version)
    if registry is not None:
        try:
            registry.register(
                model=model,
                version=version,
                description=(
                    f"Trained on {cfg.lookback_days}d of {cfg.symbol} {cfg.timeframe}"
                ),
                tags=["historical", cfg.symbol, cfg.timeframe],
            )
        except Exception as exc:
            logger.warning("Model registry error: %s", exc)
    return artifact
