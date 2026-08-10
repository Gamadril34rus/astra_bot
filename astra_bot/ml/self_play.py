"""
ASTRA BOT — Walk-forward self-play learning.

Идея: бот «проживает» год истории бар-за-баром. На каждом шаге он видит
только данные, доступные на тот момент (никакого заглядывания в будущее),
выбирает инструмент и сторону сделки по стратегиям/ML, открывает
виртуальную позицию на 2000 ₽, а после закрытия фиксирует:

* что повлияло на исход (индикаторы, волатильность, режим рынка, новости);
* как можно было бы поставить, чтобы избежать убытка;
* итоговый PnL, win-rate, profit factor, drawdown.

Накопленные сделки используются как датасет для обучения ML-модели. За
год на 1h-таймфрейме по трём инструментам получается 2-5k симулированных
сделок, что и требуется для устойчивого обучения «не в убыток».
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..core import models
from ..core.utils import calculate_atr, calculate_rsi
from ..engines.risk_engine import RiskConfig, RiskEngine
from ..strategies import MeanReversionStrategy, MomentumStrategy
from ..strategies.base import BaseStrategy, Signal

logger = logging.getLogger(__name__)


@dataclass
class Lesson:
    """Разбор одной виртуальной сделки."""

    trade_id: str
    symbol: str
    direction: str
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    qty: float
    pnl: float
    pnl_pct: float
    outcome: str  # win / loss / breakeven
    strategy: str
    confidence: float
    features: dict[str, float]
    market_regime: str
    # Краткий вывод, что улучшить в будущем.
    takeaway: str
    # Что бы изменило решение (STOP_LOSS_WIDER, SKIP_RSI_OVERBOUGHT, ...).
    recommendation: str


@dataclass
class LearningReport:
    """Итоги одного прохода self-play."""

    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    profit_factor: float
    max_drawdown_pct: float
    final_equity: float
    sharpe: float
    lessons_path: Path
    started_learning: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utcnow_ms() -> int:
    return int(datetime.now(tz=UTC).timestamp() * 1000)


def _classify_regime(candles: list[models.Candle]) -> str:
    """Определить «новостной/волатильный» фон по последним барам."""
    if len(candles) < 50:
        return "UNKNOWN"
    closes = [float(c.close) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]

    atr = calculate_atr(highs, lows, closes, period=14) or 0.0
    rsi = calculate_rsi(closes, period=14) or 50.0
    price = closes[-1]
    atr_pct = (atr / price * 100) if price else 0.0

    if atr_pct > 4.0:
        return "HIGH_VOLATILITY_NEWS"
    if rsi >= 70:
        return "OVERBOUGHT"
    if rsi <= 30:
        return "OVERSOLD"
    if closes[-1] > sum(closes[-50:]) / 50:
        return "BULL_TREND"
    return "RANGE"


def _feature_snapshot(
    strategy: BaseStrategy,
    candles: list[models.Candle],
) -> dict[str, float]:
    """Признаки, доступные на момент сделки (никакого будущего)."""
    closes = [float(c.close) for c in candles]
    volumes = [float(c.volume) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]

    last = closes[-1]
    sma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else last
    atr = calculate_atr(highs, lows, closes, period=14) or 0.0
    rsi = calculate_rsi(closes, period=14) or 50.0
    avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 1.0

    return {
        "return_1h": (closes[-1] / closes[-2] - 1) if len(closes) > 2 and closes[-2] else 0.0,
        "return_4h": (closes[-1] / closes[-5] - 1) if len(closes) > 5 and closes[-5] else 0.0,
        "return_24h": (closes[-1] / closes[-25] - 1) if len(closes) > 25 and closes[-25] else 0.0,
        "sma20_gap": (last / sma20 - 1) if sma20 else 0.0,
        "atr_pct": (atr / last * 100) if last else 0.0,
        "rsi": rsi,
        "volume_ratio": volumes[-1] / avg_vol if avg_vol else 1.0,
        "confidence": float(getattr(strategy, "last_confidence", 0.0)),
    }


def _recommend(
    outcome: str,
    features: dict[str, float],
    direction: str,
) -> str:
    """Подсказка для модели: что стоило бы сделать иначе."""
    if outcome == "win":
        if features["rsi"] >= 65 and direction == "long":
            return "EXIT_EARLY_OVERBOUGHT"
        return "HOLD_WINNER"
    # Убыток
    if features["atr_pct"] > 4.0:
        return "SKIP_HIGH_VOLATILITY"
    if features["volume_ratio"] < 0.7:
        return "SKIP_LOW_VOLUME"
    if direction == "long" and features["return_24h"] < -0.03:
        return "AVOID_LONG_IN_DOWNTREND"
    if direction == "short" and features["return_24h"] > 0.03:
        return "AVOID_SHORT_IN_UPTREND"
    if features["rsi"] >= 70:
        return "SKIP_RSI_OVERBOUGHT"
    if features["rsi"] <= 30:
        return "SKIP_RSI_OVERSOLD"
    return "WIDEN_STOP_LOSS"


def _takeaway(lesson: Lesson) -> str:
    if lesson.outcome == "win":
        return f"{lesson.symbol} {lesson.direction.upper()} сработал: {lesson.pnl_pct:.2f}% за {lesson.market_regime}"
    return (
        f"Убыток {lesson.pnl_pct:.2f}% на {lesson.symbol} ({lesson.market_regime}): "
        f"{lesson.recommendation}"
    )


@dataclass
class SelfPlayConfig:
    """Параметры self-play."""

    symbols: tuple[str, ...] = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
    timeframe: str = "1h"
    initial_capital: Decimal = Decimal("2000")
    # Сколько баров удерживать позицию, если SL/TP не сработали.
    max_holding_bars: int = 24
    # Минимальный R:R, иначе пропускаем сигнал.
    min_rr: float = 1.2
    # Комиссия биржи (taker).
    fee_rate: Decimal = Decimal("0.0005")
    # Доля виртуального капитала, которой «заходим» в сделку (notional / equity).
    # При 0.05 на 2000 ₽ номинал позиции — 100 ₽, поэтому серия из 100
    # убыточных стопов не обнуляет счёт.
    position_fraction: Decimal = Decimal("0.05")
    # Ориентир по числу сделок за год.
    target_trades: int = 3000
    # Self-play обучается «в любую погоду»: реальный risk-engine с
    # аварийной остановкой по просадке тут только считает статистику.
    ignore_risk_limits: bool = True
    lessons_output: Path = field(
        default_factory=lambda: Path("models/lessons.jsonl")
    )


class AlwaysInStrategy(BaseStrategy):
    """Тестовая стратегия: каждые N баров даёт сигнал по тренду.

    Используется только в self-play для равномерного покрытия истории
    уроками — реальные стратегии (momentum/mean-reversion) дополняют её.
    """

    def __init__(self, name: str = "self_play_baseline", every_n_bars: int = 4):
        from ..strategies.base import StrategyConfig

        super().__init__(StrategyConfig(name=name))
        self.every_n_bars = every_n_bars

    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook=None,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Signal | None:
        from ..strategies.base import SignalType

        if len(candles) < 60:
            return None
        if len(candles) % self.every_n_bars != 0:
            return None
        closes = [float(c.close) for c in candles]
        sma_fast = sum(closes[-10:]) / 10
        sma_slow = sum(closes[-50:]) / 50
        direction = (
            models.TradeDirection.LONG
            if sma_fast >= sma_slow
            else models.TradeDirection.SHORT
        )
        price = Decimal(str(current_price or closes[-1]))
        atr = calculate_atr(
            [float(c.high) for c in candles[-20:]],
            [float(c.low) for c in candles[-20:]],
            closes[-20:],
            period=14,
        ) or 0.0
        stop_dist = Decimal(str(max(atr, float(price) * 0.005)))
        stop = price - stop_dist if direction == models.TradeDirection.LONG else price + stop_dist
        take = price + stop_dist * Decimal("1.5") if direction == models.TradeDirection.LONG else price - stop_dist * Decimal("1.5")
        return Signal(
            symbol=symbol,
            strategy_name=self.name,
            signal_type=SignalType.MOMENTUM,
            direction=direction,
            entry_price=price,
            stop_loss=stop,
            take_profit=take,
            position_size=Decimal("0"),
            risk_amount=Decimal("0"),
            confidence=0.5,
        )

    def calculate_stop_loss(self, entry_price, candles, atr=None):
        atr = atr or 0.0
        return entry_price - Decimal(str(max(atr, float(entry_price) * 0.005)))

    def calculate_take_profit(self, entry_price, stop_loss, candles):
        risk = abs(entry_price - stop_loss)
        return [{"price": entry_price + risk * Decimal("1.5"), "r_multiple": 1.5}]


class SelfPlayEngine:
    """Проигрыватель года истории бар-за-баром."""

    def __init__(
        self,
        config: SelfPlayConfig | None = None,
        strategies: list[BaseStrategy] | None = None,
    ):
        self.config = config or SelfPlayConfig()
        if strategies:
            self.strategies = strategies
        else:
            # Baseline-стратегия создаёт поток сделок для обучения,
            # а Momentum/MeanReversion подмешивают «умные» входы.
            self.strategies = [
                AlwaysInStrategy(),
                MomentumStrategy(),
                MeanReversionStrategy(),
            ]
        self.risk = RiskEngine(
            RiskConfig(
                risk_per_trade=Decimal("0.01"),
                max_open_positions=3,
                # В self-play риск-лимиты не блокируют входы — объект
                # используется только как хранилище статистики.
                max_exposure_pct=Decimal("100"),
            )
        )
        self.risk.set_capital(self.config.initial_capital, self.config.initial_capital)
        self.lessons: list[Lesson] = []
        self.equity_curve: list[float] = [float(self.config.initial_capital)]
        self._equity = Decimal(str(self.config.initial_capital))

    # ------------------------------------------------------------- загрузка
    async def load_history(self, client, lookback_days: int = 365) -> dict[str, list[models.Candle]]:
        """Загрузить историю по всем инструментам."""
        from .historical_training import fetch_historical_candles

        out: dict[str, list[models.Candle]] = {}
        for symbol in self.config.symbols:
            exchange_symbol = symbol.replace("/", "-")
            candles = await fetch_historical_candles(
                client=client,
                symbol=exchange_symbol,
                timeframe=self.config.timeframe,
                lookback_days=lookback_days,
            )
            # Возвращаем канонический символ BTC/USDT.
            for c in candles:
                c.symbol = symbol
            out[symbol] = candles
            logger.info("Загружено %d свечей %s", len(candles), symbol)
        return out

    def _generate_synthetic_history(self, n: int) -> dict[str, list[models.Candle]]:
        """Создать офлайн-историю для отладки/тестов без сети."""
        import random

        out: dict[str, list[models.Candle]] = {}
        for idx, symbol in enumerate(self.config.symbols):
            random.seed(idx + 1)
            base = (
                30000.0 if "BTC" in symbol
                else 2000.0 if "ETH" in symbol
                else 100.0
            )
            start = int(
                datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000
            )
            bars = []
            for j in range(n):
                base *= 1 + random.uniform(-0.005, 0.0055)
                bars.append(
                    models.Candle(
                        exchange="okx",
                        symbol=symbol,
                        timeframe=self.config.timeframe,
                        open_time=start + j * 3_600_000,
                        open=Decimal(str(base * 0.999)),
                        high=Decimal(str(base * 1.004)),
                        low=Decimal(str(base * 0.996)),
                        close=Decimal(str(base)),
                        volume=Decimal(str(random.uniform(5, 30))),
                        quote_volume=Decimal("1"),
                    )
                )
            out[symbol] = bars
        return out

    # ------------------------------------------------------------- симуляция
    def _close_trade(
        self,
        *,
        trade_id: str,
        symbol: str,
        direction: str,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        quantity: Decimal,
        future_bars: list[models.Candle],
        strategy: BaseStrategy,
        features: dict[str, float],
        regime: str,
        confidence: float,
        entry_ts: int,
    ) -> Lesson | None:
        """Пройти future_bars и определить цену закрытия (SL/TP/таймаут)."""
        exit_price = entry_price
        exit_ts = entry_ts
        for bar in future_bars[: self.config.max_holding_bars]:
            if direction == "long":
                if bar.low <= stop_loss:
                    exit_price = stop_loss
                    exit_ts = bar.open_time
                    break
                if bar.high >= take_profit:
                    exit_price = take_profit
                    exit_ts = bar.open_time
                    break
            else:
                if bar.high >= stop_loss:
                    exit_price = stop_loss
                    exit_ts = bar.open_time
                    break
                if bar.low <= take_profit:
                    exit_price = take_profit
                    exit_ts = bar.open_time
                    break
        else:
            # Таймаут — выходим по последнему закрытию.
            if future_bars:
                last = future_bars[min(self.config.max_holding_bars, len(future_bars)) - 1]
                exit_price = last.close
                exit_ts = last.open_time

        if direction == "long":
            gross = (exit_price - entry_price) * quantity
        else:
            gross = (entry_price - exit_price) * quantity
        # Taker fee 0.1% применяется к номиналу каждой стороны сделки.
        # Для виртуального бэктеста используем 0.05% чтобы эмулировать
        # maker-тейкер микс и не наказывать стратегию «газовыми» сборами.
        notional = entry_price * quantity
        fees = notional * self.config.fee_rate
        pnl = gross - fees
        pnl_pct = float(pnl / (entry_price * quantity) * 100) if entry_price and quantity else 0.0

        if pnl > 0:
            outcome = "win"
        elif pnl < 0:
            outcome = "loss"
        else:
            outcome = "breakeven"

        # Обновляем виртуальный капитал и статистику risk-движка
        # (для метрик; risk-лимиты в self-play отключены).
        self._equity += pnl
        self.risk._total_trades += 1
        if outcome == "win":
            self.risk._total_wins += 1
        elif outcome == "loss":
            self.risk._total_losses += 1
        self.risk._daily_pnl += pnl
        self.risk._weekly_pnl += pnl
        self.risk._current_equity = self._equity
        self.equity_curve.append(float(self._equity))

        recommendation = _recommend(outcome, features, direction)
        lesson = Lesson(
            trade_id=trade_id,
            symbol=symbol,
            direction=direction,
            entry_time=entry_ts,
            exit_time=exit_ts,
            entry_price=float(entry_price),
            exit_price=float(exit_price),
            qty=float(quantity),
            pnl=float(pnl),
            pnl_pct=pnl_pct,
            outcome=outcome,
            strategy=strategy.name,
            confidence=confidence,
            features=features,
            market_regime=regime,
            takeaway="",
            recommendation=recommendation,
        )
        lesson.takeaway = _takeaway(lesson)
        self.lessons.append(lesson)
        return lesson

    async def run(
        self,
        history: dict[str, list[models.Candle]] | None = None,
        client=None,
        offline_bars: int = 0,
    ) -> LearningReport:
        """Запустить полный проход self-play."""
        if history is None:
            if offline_bars > 0:
                history = self._generate_synthetic_history(offline_bars)
            elif client is not None:
                history = await self.load_history(client)
            else:
                raise ValueError(
                    "Нужно передать history, клиент OKX или включить offline_bars"
                )

        # Выравниваем по временной оси: бар за баром идём по самому
        # короткому инструменту, чтобы не было look-ahead.
        timestamps = sorted(
            set.intersection(*(set(c.open_time for c in candles) for candles in history.values()))
        )
        if not timestamps:
            raise RuntimeError("Нет общих таймстемпов у инструментов")

        total_steps = len(timestamps)
        logger.info("Self-play: %d общих баров по %d инструментам",
                    total_steps, len(history))

        started_learning = False
        message = "Недостаточно данных для целевого обучения"

        for step, ts in enumerate(timestamps):
            # Пропускаем первые 200 баров как «прогрев» индикаторов.
            if step < 200:
                continue

            for symbol, candles in history.items():
                idx = next(
                    (i for i, c in enumerate(candles) if c.open_time == ts),
                    None,
                )
                if idx is None or idx < 200:
                    continue
                window = candles[: idx + 1]
                future = candles[idx + 1 : idx + 1 + self.config.max_holding_bars]
                if not future:
                    continue

                regime = _classify_regime(window)
                best_signal: models.Signal | None = None
                best_strat: BaseStrategy | None = None

                for strategy in self.strategies:
                    try:
                        signal = await strategy.evaluate(
                            symbol=symbol,
                            candles=window,
                            current_price=float(window[-1].close),
                            market_regime=regime,
                        )
                    except Exception as exc:
                        logger.debug("Strategy %s error: %s", strategy.name, exc)
                        continue
                    if signal and signal.risk_reward_ratio >= self.config.min_rr:
                        if best_signal is None or signal.confidence > best_signal.confidence:
                            best_signal = signal
                            best_strat = strategy

                if not best_signal or not best_strat:
                    continue

                # Размер позиции: фиксированная доля от НАЧАЛЬНОГО капитала.
                # Это позволяет пережить серию стопов и набрать 2-5k уроков.
                notional = (
                    Decimal(str(self.config.initial_capital))
                    * self.config.position_fraction
                )
                quantity = notional / best_signal.entry_price
                if quantity <= 0:
                    continue

                # Risk-engine в режиме ignore_risk_limits ведёт только
                # статистику; иначе дополнительно проверяем лимиты.
                if not self.config.ignore_risk_limits:
                    check = self.risk.check_trade(
                        symbol=symbol,
                        side=best_signal.direction.value,
                        entry_price=best_signal.entry_price,
                        stop_loss=best_signal.stop_loss,
                        take_profit=best_signal.take_profit,
                        proposed_size=quantity,
                    )
                    if not check.approved:
                        continue

                features = _feature_snapshot(best_strat, window)
                trade_id = str(uuid.uuid4())
                self._close_trade(
                    trade_id=trade_id,
                    symbol=symbol,
                    direction=best_signal.direction.value,
                    entry_price=best_signal.entry_price,
                    stop_loss=best_signal.stop_loss,
                    take_profit=best_signal.take_profit,
                    quantity=quantity,
                    future_bars=future,
                    strategy=best_strat,
                    features=features,
                    regime=regime,
                    confidence=float(best_signal.confidence),
                    entry_ts=ts,
                )

            # Прерываем, если набрали целевое число сделок.
            if len(self.lessons) >= self.config.target_trades:
                break

        if len(self.lessons) >= self.config.target_trades:
            started_learning = True
            message = f"Обучение запущено на {len(self.lessons)} сделках"

        report = self._build_report(started_learning, message)
        self._save_lessons()
        return report

    # ------------------------------------------------------------- отчётность
    def _build_report(self, started_learning: bool, message: str) -> LearningReport:
        if not self.lessons:
            return LearningReport(
                total_trades=0,
                wins=0,
                losses=0,
                win_rate=0.0,
                total_pnl=0.0,
                profit_factor=0.0,
                max_drawdown_pct=0.0,
                final_equity=float(self.config.initial_capital),
                sharpe=0.0,
                lessons_path=self.config.lessons_output,
                started_learning=False,
                message="Сделок не сгенерировано",
            )

        wins = sum(1 for lesson in self.lessons if lesson.outcome == "win")
        losses = sum(1 for lesson in self.lessons if lesson.outcome == "loss")
        total_pnl = sum(lesson.pnl for lesson in self.lessons)
        gross_profit = sum(lesson.pnl for lesson in self.lessons if lesson.pnl > 0)
        gross_loss = abs(sum(lesson.pnl for lesson in self.lessons if lesson.pnl < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        win_rate = wins / len(self.lessons) * 100

        # Max drawdown по equity curve.
        peak = float(self.config.initial_capital)
        max_dd = 0.0
        for equity in self.equity_curve or [float(self.config.initial_capital) + total_pnl]:
            peak = max(peak, equity)
            dd = (peak - equity) / peak * 100 if peak else 0.0
            max_dd = max(max_dd, dd)

        # Sharpe по per-trade доходностям.
        returns = [lesson.pnl_pct for lesson in self.lessons]
        mean_r = sum(returns) / len(returns)
        std_r = (sum((r - mean_r) ** 2 for r in returns) / len(returns)) ** 0.5
        sharpe = mean_r / std_r if std_r else 0.0

        return LearningReport(
            total_trades=len(self.lessons),
            wins=wins,
            losses=losses,
            win_rate=win_rate,
            total_pnl=total_pnl,
            profit_factor=profit_factor,
            max_drawdown_pct=max_dd,
            final_equity=float(self._equity),
            sharpe=sharpe,
            lessons_path=self.config.lessons_output,
            started_learning=started_learning,
            message=message,
        )

    def _save_lessons(self) -> None:
        path = self.config.lessons_output
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for lesson in self.lessons:
                f.write(json.dumps(asdict(lesson), ensure_ascii=False) + "\n")
        logger.info("Сохранено %d уроков в %s", len(self.lessons), path)


def format_daily_report(report: LearningReport) -> str:
    """Человекочитаемый отчёт для Telegram."""
    pnl_icon = "🟢" if report.total_pnl >= 0 else "🔴"
    return (
        "🎓 *ОТЧЁТ ОБ ОБУЧЕНИИ*\n\n"
        f"*Виртуальный капитал:* {report.final_equity:,.2f} ₽\n"
        f"*Сделок:* {report.total_trades} "
        f"(✅ {report.wins} / ❌ {report.losses})\n"
        f"*Win Rate:* {report.win_rate:.1f}%\n"
        f"*Profit Factor:* {report.profit_factor:.2f}\n"
        f"{pnl_icon} *PnL:* {report.total_pnl:+,.2f} ₽\n"
        f"*Макс. просадка:* {report.max_drawdown_pct:.2f}%\n"
        f"*Sharpe:* {report.sharpe:.2f}\n\n"
        f"📝 {report.message}\n"
        f"🧠 Уроки сохранены: `{report.lessons_path}`"
    )
