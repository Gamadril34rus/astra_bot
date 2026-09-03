"""
ASTRA BOT — План торговли на ближайшие 24 часа.

Задача модуля — на текущем срезе рынка собрать сигналы всех стратегий
по всем инструментам, прогнать их через обученную weekly-модель и
отфильтровать так, чтобы в план попали только ставки с высокой
предсказанной вероятностью прибыли и приемлемым R:R.

Это НЕ исполнение ордеров — только morning shortlist, который
отправляется в Telegram и логируется.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..core import models
from .feature_pipeline import FeaturePipeline
from .weekly_learner import FEATURE_COLUMNS

logger = logging.getLogger(__name__)


@dataclass
class PlannedTrade:
    symbol: str
    direction: str
    strategy: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    strategy_confidence: float
    ml_probability: float
    regime: str
    expected_value: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _probability_from_model(
    model, features: dict[str, float]
) -> float | None:
    """Вернуть P(win) из weekly-модели, либо None."""
    if model is None or not getattr(model, "is_fitted", False):
        return None
    try:
        vector = np.array(
            [[features.get(col, 0.0) for col in FEATURE_COLUMNS]],
            dtype=float,
        )
        proba = model.model.predict_proba(vector)[0]
        return float(proba[1]) if len(proba) > 1 else float(proba[0])
    except Exception as exc:  # pragma: no cover - защитный путь
        logger.debug("ML predict failed: %s", exc)
        return None


async def build_daily_plan(
    history: dict[str, list[models.Candle]],
    strategies: list,
    model_path: Path = Path("models/current.pkl"),
    min_ml_probability: float = 0.58,
    min_rr: float = 1.2,
    top_k: int = 5,
    pipeline: FeaturePipeline | None = None,
) -> list[PlannedTrade]:
    """Собрать отсортированный план сделок на ближайшие 24 часа.

    Args:
        history: свечи по инструментам, как их отдаёт клиент биржи (BingX).
        strategies: список стратегий с методом ``evaluate``.
        model_path: путь к ``current.pkl`` weekly-модели.
        min_ml_probability: минимальная предсказанная вероятность win.
        min_rr: минимальное соотношение reward/risk.
        top_k: сколько сделок вернуть в итоге.
        pipeline: фича-пайплайн для ML (по умолчанию новый).
    """
    model = None
    if model_path.exists():
        try:
            from .model_trainer import MLModel

            model = MLModel.load(str(model_path))
        except Exception as exc:
            logger.warning("Не загрузил модель из %s: %s", model_path, exc)

    pipeline = pipeline or FeaturePipeline()
    candidates: list[PlannedTrade] = []

    for symbol, candles in history.items():
        if len(candles) < 60:
            continue
        window = candles
        current_price = float(candles[-1].close)
        cross = _cross_snapshot(history, candles[-1].open_time)
        regime = _simple_regime(window)

        for strategy in strategies:
            try:
                import inspect

                maybe = strategy.evaluate(
                    symbol=symbol,
                    candles=window,
                    current_price=current_price,
                    market_regime=regime,
                )
                signal = await maybe if inspect.isawaitable(maybe) else maybe
            except Exception as exc:
                logger.debug("Strategy %s failed on %s: %s", strategy.name, symbol, exc)
                continue
            if signal is None:
                continue
            rr = signal.risk_reward_ratio
            if rr < min_rr:
                continue

            features = _features_for_signal(
                pipeline, strategy, window, cross
            )
            prob = _probability_from_model(model, features)

            # Без модели берём стратегию с высокой уверенностью;
            # с моделью — только если вероятность прошла порог.
            if prob is None:
                if signal.confidence < 0.6:
                    continue
                prob = float(signal.confidence)
                reason = "strategy-only (нет ML-модели)"
            else:
                if prob < min_ml_probability:
                    continue
                reason = f"ML win prob {prob:.0%}"

            risk = abs(float(signal.entry_price - signal.stop_loss))
            reward = abs(float(signal.take_profit - signal.entry_price))
            ev = prob * reward - (1 - prob) * risk

            candidates.append(
                PlannedTrade(
                    symbol=symbol,
                    direction=signal.direction.value,
                    strategy=strategy.name,
                    entry_price=float(signal.entry_price),
                    stop_loss=float(signal.stop_loss),
                    take_profit=float(signal.take_profit),
                    risk_reward=rr,
                    strategy_confidence=float(signal.confidence),
                    ml_probability=prob,
                    regime=regime,
                    expected_value=ev,
                    reason=reason,
                )
            )

    candidates.sort(key=lambda t: t.expected_value, reverse=True)
    return candidates[:top_k]


def _features_for_signal(
    pipeline: FeaturePipeline,
    strategy,
    candles: list[models.Candle],
    cross: dict[str, float],
) -> dict[str, float]:
    """Собрать тот же набор признаков, что пишет self-play."""
    from .self_play import _feature_snapshot

    features = _feature_snapshot(strategy, candles)
    features.update(cross)
    return features


def _cross_snapshot(
    history: dict[str, list[models.Candle]], open_time: int
) -> dict[str, float]:
    """Доходности остальных инструментов на момент ``open_time``."""
    cross: dict[str, float] = {}
    for symbol, bars in history.items():
        for i, bar in enumerate(bars):
            if bar.open_time == open_time and i >= 1:
                prev = float(bars[i - 1].close)
                cur = float(bar.close)
                cross[f"{symbol}_1h"] = cur / prev - 1 if prev else 0.0
                break
    return cross


def _simple_regime(candles: list[models.Candle]) -> str:
    """Упрощённый режим рынка для плана."""
    closes = [float(c.close) for c in candles[-50:]]
    if len(closes) < 50:
        return "UNKNOWN"
    sma = sum(closes) / len(closes)
    if closes[-1] > sma * 1.02:
        return "BULL_TREND"
    if closes[-1] < sma * 0.98:
        return "BEAR_TREND"
    return "RANGE"


def format_plan(plan: list[PlannedTrade]) -> str:
    """Человекочитаемый план для Telegram."""
    if not plan:
        return (
            "📋 *План на сегодня:* сделок нет.\n\n"
            "ML-модель не нашла сигналов с приемлемой вероятностью "
            "прибыли — лучше побыть вне рынка."
        )
    lines = [f"📋 *План на ближайшие 24 часа* — {len(plan)} сделок:"]
    for i, t in enumerate(plan, start=1):
        arrow = "🟢 Лонг" if t.direction == "long" else "🔴 Шорт"
        lines.append(
            f"\n*{i}. {t.symbol}* — {arrow} ({t.strategy})\n"
            f"  Вход: {t.entry_price:,.2f}\n"
            f"  Стоп: {t.stop_loss:,.2f} / Тейк: {t.take_profit:,.2f}\n"
            f"  R:R = {t.risk_reward:.2f}, ML win = {t.ml_probability:.0%}\n"
            f"  EV = {t.expected_value:+.2f}, режим: {t.regime}\n"
            f"  Причина: {t.reason}"
        )
    return "\n".join(lines)
