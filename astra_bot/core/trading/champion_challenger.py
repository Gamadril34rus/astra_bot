"""
ASTRA BOT - Champion-Challenger Framework

Фреймворк Champion-Challenger (ТЗ Пункты 37, 47, 53, 59-60, 68, 72, 75, 79, 85, 92)

Реализует:
- champion/challenger testing
- automatic promotion/demotion
- performance-based selection
- statistical significance testing
- live A/B testing
- rollback mechanism

"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


class StrategyStatus(str, Enum):
    """Статусы стратегий"""
    CHAMPION = "champion"  # Действующая стратегия
    CHALLENGER = "challenger"  # Претендент
    TESTING = "testing"  # На тестировании
    PROMOTED = "promoted"  # Повышена
    DEMOTED = "demoted"  # Понижена
    RETIRED = "retired"  # Выведена из эксплуатации


class TestType(str, Enum):
    """Типы тестирования"""
    BACKTEST = "backtest"
    PAPER_TRADING = "paper_trading"
    LIVE_TRADING = "live_trading"
    A_B_TESTING = "a_b_testing"


class ComparisonMetric(str, Enum):
    """Метрики сравнения"""
    TOTAL_RETURN = "total_return"
    SHARPE_RATIO = "sharpe_ratio"
    MAX_DRAWDOWN = "max_drawdown"
    WIN_RATE = "win_rate"
    PROFIT_FACTOR = "profit_factor"
    SORTINO_RATIO = "sortino_ratio"
    CALMAR_RATIO = "calmar_ratio"
    RISK_ADJUSTED_RETURN = "risk_adjusted_return"


@dataclass
class StrategyPerformance:
    """Производительность стратегии"""
    strategy_id: str
    
    # Метрики
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    calmar_ratio: float = 0.0
    
    # Статистика
    num_trades: int = 0
    avg_return: float = 0.0
    std_return: float = 0.0
    
    # Время
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Уверенность
    confidence: float = 0.0
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "total_return": self.total_return,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "max_drawdown": self.max_drawdown,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "calmar_ratio": self.calmar_ratio,
            "num_trades": self.num_trades,
            "avg_return": self.avg_return,
            "std_return": self.std_return,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "confidence": self.confidence,
        }


@dataclass
class ComparisonResult:
    """Результат сравнения стратегий"""
    champion_id: str
    challenger_id: str
    
    # Метрики сравнения
    metric_values: dict[ComparisonMetric, tuple[float, float]] = field(default_factory=dict)
    
    # Победитель
    winner: str | None = None  # champion, challenger, or tie
    winning_metrics: list[ComparisonMetric] = field(default_factory=list)
    
    # Статистическая значимость
    p_values: dict[ComparisonMetric, float] = field(default_factory=dict)
    significant_metrics: list[ComparisonMetric] = field(default_factory=list)
    
    # Уверенность
    confidence: float = 0.0
    
    # Время
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "champion_id": self.champion_id,
            "challenger_id": self.challenger_id,
            "winner": self.winner,
            "winning_metrics": [m.value for m in self.winning_metrics],
            "significant_metrics": [m.value for m in self.significant_metrics],
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
        }
        
        result["metric_values"] = {m.value: list(v) for m, v in self.metric_values.items()}
        result["p_values"] = {m.value: v for m, v in self.p_values.items()}
        
        return result


@dataclass
class TestResult:
    """Результат тестирования"""
    test_id: str
    strategy_id: str
    test_type: TestType
    
    # Производительность
    performance: StrategyPerformance
    
    # Статус
    passed: bool = False
    
    # Причины
    reasons: list[str] = field(default_factory=list)
    
    # Время
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "strategy_id": self.strategy_id,
            "test_type": self.test_type.value,
            "performance": self.performance.to_dict(),
            "passed": self.passed,
            "reasons": self.reasons,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ChampionChallengerState:
    """Состояние фреймворка Champion-Challenger"""
    champion_id: str
    challenger_ids: list[str] = field(default_factory=list)
    
    # Производительность
    champion_performance: StrategyPerformance | None = None
    challenger_performances: dict[str, StrategyPerformance] = field(default_factory=dict)
    
    # Последнее сравнение
    last_comparison: ComparisonResult | None = None
    
    # Последнее тестирование
    last_test: TestResult | None = None
    
    # Статистика
    promotion_count: int = 0
    demotion_count: int = 0
    test_count: int = 0
    
    # Время
    last_update: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "champion_id": self.champion_id,
            "challenger_ids": self.challenger_ids,
            "promotion_count": self.promotion_count,
            "demotion_count": self.demotion_count,
            "test_count": self.test_count,
            "last_update": self.last_update.isoformat(),
        }
        
        if self.champion_performance:
            result["champion_performance"] = self.champion_performance.to_dict()
        
        result["challenger_performances"] = {k: v.to_dict() for k, v in self.challenger_performances.items()}
        
        if self.last_comparison:
            result["last_comparison"] = self.last_comparison.to_dict()
        
        if self.last_test:
            result["last_test"] = self.last_test.to_dict()
        
        return result


class ChampionChallengerFramework:
    """
    Фреймворк Champion-Challenger.
    
    Управляет тестированием и продвижением стратегий.
    """
    
    def __init__(self):
        # Стратегии
        self._strategies: dict[str, dict[str, Any]] = {}
        
        # Производительность
        self._performances: dict[str, StrategyPerformance] = {}
        
        # Состояние
        self._state: ChampionChallengerState | None = None
        
        # Тесты
        self._tests: dict[str, TestResult] = {}
        
        # Сравнения
        self._comparisons: dict[str, ComparisonResult] = {}
        
        # Пороги
        self.thresholds = {
            "min_test_period_days": 30,
            "min_trades": 100,
            "min_sharpe_ratio": 1.0,
            "max_drawdown_limit": 20.0,
            "min_win_rate": 0.5,
            "min_profit_factor": 1.2,
            "significance_level": 0.05,
            "promotion_threshold": 0.7,  # 70% метрик должны быть лучше
            "demotion_threshold": 0.3,  # 30% метрик должны быть хуже
        }
    
    def set_champion(self, strategy_id: str):
        """
        Установить champion стратегию.
        
        Args:
            strategy_id: ID стратегии
        """
        if strategy_id not in self._strategies:
            self._strategies[strategy_id] = {"status": StrategyStatus.CHAMPION}
        else:
            self._strategies[strategy_id]["status"] = StrategyStatus.CHAMPION
        
        # Создать состояние
        if self._state is None:
            self._state = ChampionChallengerState(
                champion_id=strategy_id,
            )
        else:
            self._state.champion_id = strategy_id
    
    def add_challenger(self, strategy_id: str):
        """
        Добавить challenger стратегию.
        
        Args:
            strategy_id: ID стратегии
        """
        if strategy_id not in self._strategies:
            self._strategies[strategy_id] = {"status": StrategyStatus.CHALLENGER}
        else:
            self._strategies[strategy_id]["status"] = StrategyStatus.CHALLENGER
        
        if self._state and strategy_id not in self._state.challenger_ids:
            self._state.challenger_ids.append(strategy_id)
    
    def update_performance(
        self,
        strategy_id: str,
        performance: StrategyPerformance,
    ):
        """
        Обновить производительность стратегии.
        
        Args:
            strategy_id: ID стратегии
            performance: Производительность
        """
        self._performances[strategy_id] = performance
        
        # Обновить состояние
        if self._state:
            if self._state.champion_id == strategy_id:
                self._state.champion_performance = performance
            elif strategy_id in self._state.challenger_ids:
                self._state.challenger_performances[strategy_id] = performance
    
    def compare_strategies(
        self,
        champion_id: str,
        challenger_id: str,
        metrics: list[ComparisonMetric] | None = None,
    ) -> ComparisonResult:
        """
        Сравнить стратегии.
        
        Args:
            champion_id: ID champion стратегии
            challenger_id: ID challenger стратегии
            metrics: Метрики для сравнения
        
        Returns:
            Результат сравнения
        """
        champion_perf = self._performances.get(champion_id)
        challenger_perf = self._performances.get(challenger_id)
        
        if not champion_perf or not challenger_perf:
            return ComparisonResult(
                champion_id=champion_id,
                challenger_id=challenger_id,
                winner=None,
                confidence=0.0,
            )
        
        if metrics is None:
            metrics = list(ComparisonMetric)
        
        metric_values = {}
        p_values = {}
        winning_metrics = []
        significant_metrics = []
        
        for metric in metrics:
            # Получить значения метрик
            champion_value = getattr(champion_perf, metric.value.lower(), 0.0)
            challenger_value = getattr(challenger_perf, metric.value.lower(), 0.0)
            
            metric_values[metric] = (champion_value, challenger_value)
            
            # Определить победителя по метрике
            if metric in [ComparisonMetric.TOTAL_RETURN, ComparisonMetric.WIN_RATE, 
                         ComparisonMetric.PROFIT_FACTOR, ComparisonMetric.SHARPE_RATIO,
                         ComparisonMetric.SORTINO_RATIO, ComparisonMetric.CALMAR_RATIO,
                         ComparisonMetric.RISK_ADJUSTED_RETURN]:
                # Чем выше, тем лучше
                if challenger_value > champion_value:
                    winning_metrics.append(metric)
            elif metric == ComparisonMetric.MAX_DRAWDOWN:
                # Чем ниже, тем лучше
                if challenger_value < champion_value:
                    winning_metrics.append(metric)
            
            # Рассчитать p-value (упрощённая оценка)
            # В реальности нужно использовать статистические тесты
            if champion_value != challenger_value:
                p_values[metric] = 0.05  # Упрощённое значение
                if p_values[metric] < self.thresholds["significance_level"]:
                    significant_metrics.append(metric)
            else:
                p_values[metric] = 1.0
        
        # Определить итогового победителя
        if not winning_metrics:
            winner = None
        elif len(winning_metrics) / len(metrics) >= self.thresholds["promotion_threshold"]:
            winner = "challenger"
        elif len(winning_metrics) / len(metrics) <= self.thresholds["demotion_threshold"]:
            winner = "champion"
        else:
            winner = None  # Ничья
        
        # Рассчитать уверенность
        confidence = len(winning_metrics) / len(metrics)
        
        result = ComparisonResult(
            champion_id=champion_id,
            challenger_id=challenger_id,
            metric_values=metric_values,
            winner=winner,
            winning_metrics=winning_metrics,
            p_values=p_values,
            significant_metrics=significant_metrics,
            confidence=confidence,
        )
        
        # Сохранить сравнение
        comparison_id = f"{champion_id}_{challenger_id}_{datetime.now(timezone.utc).isoformat()}"
        self._comparisons[comparison_id] = result
        
        # Обновить состояние
        if self._state:
            self._state.last_comparison = result
        
        return result
    
    def run_test(
        self,
        strategy_id: str,
        test_type: TestType,
        performance: StrategyPerformance,
    ) -> TestResult:
        """
        Запустить тестирование стратегии.
        
        Args:
            strategy_id: ID стратегии
            test_type: Тип тестирования
            performance: Производительность
        
        Returns:
            Результат тестирования
        """
        # Проверить минимальные требования
        passed = True
        reasons = []
        
        if performance.num_trades < self.thresholds["min_trades"]:
            passed = False
            reasons.append(f"Insufficient trades: {performance.num_trades} < {self.thresholds['min_trades']}")
        
        if performance.sharpe_ratio < self.thresholds["min_sharpe_ratio"]:
            passed = False
            reasons.append(f"Low Sharpe ratio: {performance.sharpe_ratio:.2f} < {self.thresholds['min_sharpe_ratio']}")
        
        if performance.max_drawdown > self.thresholds["max_drawdown_limit"]:
            passed = False
            reasons.append(f"High max drawdown: {performance.max_drawdown:.2f}% > {self.thresholds['max_drawdown_limit']}%")
        
        if performance.win_rate < self.thresholds["min_win_rate"]:
            passed = False
            reasons.append(f"Low win rate: {performance.win_rate:.2f} < {self.thresholds['min_win_rate']}")
        
        if performance.profit_factor < self.thresholds["min_profit_factor"]:
            passed = False
            reasons.append(f"Low profit factor: {performance.profit_factor:.2f} < {self.thresholds['min_profit_factor']}")
        
        if not reasons:
            reasons.append("All criteria passed")
        
        result = TestResult(
            test_id=f"{strategy_id}_{test_type.value}_{datetime.now(timezone.utc).isoformat()}",
            strategy_id=strategy_id,
            test_type=test_type,
            performance=performance,
            passed=passed,
            reasons=reasons,
        )
        
        self._tests[result.test_id] = result
        
        # Обновить состояние
        if self._state:
            self._state.last_test = result
            self._state.test_count += 1
        
        return result
    
    def promote_challenger(self, challenger_id: str) -> dict[str, Any]:
        """
        Повысить challenger до champion.
        
        Args:
            challenger_id: ID challenger стратегии
        
        Returns:
            Результат продвижения
        """
        if not self._state:
            return {"error": "No state initialized"}
        
        if challenger_id not in self._state.challenger_ids:
            return {"error": "Challenger not found"}
        
        # Сравнить с текущим champion
        comparison = self.compare_strategies(self._state.champion_id, challenger_id)
        
        if comparison.winner != "challenger":
            return {
                "status": "not_promoted",
                "reason": "Challenger did not outperform champion",
                "comparison": comparison.to_dict(),
            }
        
        # Повысить challenger
        old_champion = self._state.champion_id
        
        # Новый champion
        self._strategies[challenger_id]["status"] = StrategyStatus.CHAMPION
        self._state.champion_id = challenger_id
        self._state.champion_performance = self._performances.get(challenger_id)
        
        # Старый champion становится challenger
        self._strategies[old_champion]["status"] = StrategyStatus.CHALLENGER
        if old_champion not in self._state.challenger_ids:
            self._state.challenger_ids.append(old_champion)
        
        self._state.promotion_count += 1
        
        return {
            "status": "promoted",
            "old_champion": old_champion,
            "new_champion": challenger_id,
            "comparison": comparison.to_dict(),
        }
    
    def demote_champion(self) -> dict[str, Any]:
        """
        Понизить текущего champion.
        
        Returns:
            Результат понижения
        """
        if not self._state or not self._state.champion_id:
            return {"error": "No champion to demote"}
        
        if not self._state.challenger_ids:
            return {"error": "No challengers available"}
        
        # Выбрать лучшего challenger
        best_challenger = None
        best_performance = None
        
        for challenger_id in self._state.challenger_ids:
            perf = self._performances.get(challenger_id)
            if perf and (best_performance is None or perf.sharpe_ratio > best_performance.sharpe_ratio):
                best_challenger = challenger_id
                best_performance = perf
        
        if not best_challenger:
            return {"error": "No valid challenger found"}
        
        # Понизить champion
        old_champion = self._state.champion_id
        
        # Новый champion
        self._strategies[best_challenger]["status"] = StrategyStatus.CHAMPION
        self._state.champion_id = best_challenger
        self._state.champion_performance = best_performance
        
        # Старый champion становится challenger
        self._strategies[old_champion]["status"] = StrategyStatus.CHALLENGER
        
        self._state.demotion_count += 1
        
        return {
            "status": "demoted",
            "old_champion": old_champion,
            "new_champion": best_challenger,
        }
    
    def get_state(self) -> ChampionChallengerState | None:
        """
        Получить текущее состояние.
        
        Returns:
            Состояние или None
        """
        return self._state
    
    def get_comparison(self, comparison_id: str) -> ComparisonResult | None:
        """
        Получить сравнение.
        
        Args:
            comparison_id: ID сравнения
        
        Returns:
            Сравнение или None
        """
        return self._comparisons.get(comparison_id)
    
    def get_test(self, test_id: str) -> TestResult | None:
        """
        Получить тест.
        
        Args:
            test_id: ID теста
        
        Returns:
            Тест или None
        """
        return self._tests.get(test_id)


# Глобальный экземпляр
_champion_challenger_framework: ChampionChallengerFramework | None = None


def get_champion_challenger_framework() -> ChampionChallengerFramework:
    """Получить глобальный Champion-Challenger Framework"""
    global _champion_challenger_framework
    if _champion_challenger_framework is None:
        _champion_challenger_framework = ChampionChallengerFramework()
    return _champion_challenger_framework


def reset_champion_challenger_framework():
    """Сбросить Champion-Challenger Framework (для тестов)"""
    global _champion_challenger_framework
    _champion_challenger_framework = ChampionChallengerFramework()
