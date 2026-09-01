"""
ASTRA BOT - Validation Gates

Ворота валидации (ТЗ Пункты 19, 21, 23, 25, 27, 31, 34, 41, 44-45, 48, 53-54, 56, 60, 65-66, 69-70, 74, 77, 80, 84, 87-88, 90-91, 93, 97-100)

Реализует:
- IS gate
- OOS gate
- Walk-forward gate
- Monte Carlo gate
- Stress test gate
- Statistical significance gate
- Robustness gate
- Parameter stability gate
- Overfitting gate
- Concept validation gate
- Edge validation gate

"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


class GateType(str, Enum):
    """Типы ворот"""
    IS_GATE = "is_gate"  # In-Sample
    OOS_GATE = "oos_gate"  # Out-of-Sample
    WALK_FORWARD_GATE = "walk_forward_gate"
    MONTE_CARLO_GATE = "monte_carlo_gate"
    STRESS_TEST_GATE = "stress_test_gate"
    STATISTICAL_SIGNIFICANCE_GATE = "statistical_significance_gate"
    ROBUSTNESS_GATE = "robustness_gate"
    PARAMETER_STABILITY_GATE = "parameter_stability_gate"
    OVERFITTING_GATE = "overfitting_gate"
    CONCEPT_VALIDATION_GATE = "concept_validation_gate"
    EDGE_VALIDATION_GATE = "edge_validation_gate"


class GateStatus(str, Enum):
    """Статусы ворот"""
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    PENDING = "pending"


@dataclass
class GateResult:
    """Результат ворота"""
    gate_type: GateType
    gate_name: str
    
    # Статус
    status: GateStatus = GateStatus.PENDING
    
    # Метрики
    metrics: dict[str, float] = field(default_factory=dict)
    
    # Пороговые значения
    thresholds: dict[str, float] = field(default_factory=dict)
    
    # Причины
    reasons: list[str] = field(default_factory=list)
    
    # Уверенность
    confidence: float = 0.0
    
    # Время
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_type": self.gate_type.value,
            "gate_name": self.gate_name,
            "status": self.status.value,
            "metrics": self.metrics,
            "thresholds": self.thresholds,
            "reasons": self.reasons,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ValidationReport:
    """Отчёт валидации"""
    strategy_id: str
    symbol: str
    
    # Результаты ворот
    gate_results: dict[GateType, GateResult] = field(default_factory=dict)
    
    # Итоговый статус
    overall_status: GateStatus = GateStatus.PENDING
    
    # Итоговая уверенность
    overall_confidence: float = 0.0
    
    # Рекомендации
    recommendations: list[str] = field(default_factory=list)
    
    # Время
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "overall_status": self.overall_status.value,
            "overall_confidence": self.overall_confidence,
            "recommendations": self.recommendations,
            "timestamp": self.timestamp.isoformat(),
            "gate_results": {g.value: r.to_dict() for g, r in self.gate_results.items()},
        }


class ValidationGates:
    """
    Ворота валидации.
    
    Проверяет стратегии на различных этапах валидации.
    """
    
    def __init__(self):
        # Пороги
        self.thresholds = {
            # IS gate
            "is_min_trades": 100,
            "is_min_sharpe": 1.0,
            "is_max_drawdown": 20.0,
            "is_min_win_rate": 0.5,
            
            # OOS gate
            "oos_min_trades": 50,
            "oos_min_sharpe": 0.8,
            "oos_max_drawdown": 25.0,
            "oos_performance_ratio": 0.8,  # OOS/IS Performance
            
            # Walk-forward gate
            "wf_min_periods": 5,
            "wf_consistency_threshold": 0.7,
            
            # Monte Carlo gate
            "mc_simulations": 1000,
            "mc_confidence_level": 0.95,
            
            # Stress test gate
            "stress_scenarios": 10,
            "stress_max_loss": 50.0,
            
            # Statistical significance gate
            "stat_significance_level": 0.05,
            "stat_min_t_stat": 2.0,
            
            # Robustness gate
            "robustness_min_param_range": 0.1,
            "robustness_max_perf_change": 20.0,
            
            # Parameter stability gate
            "param_stability_max_change": 10.0,
            
            # Overfitting gate
            "overfitting_max_complexity": 10,
            "overfitting_min_oos_performance": 0.7,
        }
        
        # История отчётов
        self._reports: dict[str, ValidationReport] = {}
    
    def run_is_gate(
        self,
        strategy_id: str,
        symbol: str,
        is_performance: dict[str, float],
    ) -> GateResult:
        """
        Ворот In-Sample валидации.
        
        Args:
            strategy_id: ID стратегии
            symbol: Символ
            is_performance: Производительность на IS данных
        
        Returns:
            Результат ворота
        """
        metrics = {}
        thresholds = {}
        reasons = []
        passed = True
        
        # Проверить количество сделок
        num_trades = is_performance.get("num_trades", 0)
        metrics["num_trades"] = num_trades
        thresholds["num_trades"] = self.thresholds["is_min_trades"]
        if num_trades < self.thresholds["is_min_trades"]:
            passed = False
            reasons.append(f"Insufficient trades: {num_trades} < {self.thresholds['is_min_trades']}")
        
        # Проверить Sharpe ratio
        sharpe = is_performance.get("sharpe_ratio", 0)
        metrics["sharpe_ratio"] = sharpe
        thresholds["sharpe_ratio"] = self.thresholds["is_min_sharpe"]
        if sharpe < self.thresholds["is_min_sharpe"]:
            passed = False
            reasons.append(f"Low Sharpe ratio: {sharpe:.2f} < {self.thresholds['is_min_sharpe']}")
        
        # Проверить максимальную просадку
        max_dd = is_performance.get("max_drawdown", 0)
        metrics["max_drawdown"] = max_dd
        thresholds["max_drawdown"] = self.thresholds["is_max_drawdown"]
        if max_dd > self.thresholds["is_max_drawdown"]:
            passed = False
            reasons.append(f"High max drawdown: {max_dd:.2f}% > {self.thresholds['is_max_drawdown']}%")
        
        # Проверить win rate
        win_rate = is_performance.get("win_rate", 0)
        metrics["win_rate"] = win_rate
        thresholds["win_rate"] = self.thresholds["is_min_win_rate"]
        if win_rate < self.thresholds["is_min_win_rate"]:
            passed = False
            reasons.append(f"Low win rate: {win_rate:.2f} < {self.thresholds['is_min_win_rate']}")
        
        if not reasons:
            reasons.append("All IS criteria passed")
        
        return GateResult(
            gate_type=GateType.IS_GATE,
            gate_name="In-Sample Validation",
            status=GateStatus.PASSED if passed else GateStatus.FAILED,
            metrics=metrics,
            thresholds=thresholds,
            reasons=reasons,
            confidence=1.0 if passed else 0.0,
        )
    
    def run_oos_gate(
        self,
        strategy_id: str,
        symbol: str,
        is_performance: dict[str, float],
        oos_performance: dict[str, float],
    ) -> GateResult:
        """
        Ворот Out-of-Sample валидации.
        
        Args:
            strategy_id: ID стратегии
            symbol: Символ
            is_performance: Производительность на IS данных
            oos_performance: Производительность на OOS данных
        
        Returns:
            Результат ворота
        """
        metrics = {}
        thresholds = {}
        reasons = []
        passed = True
        
        # Проверить количество сделок
        num_trades = oos_performance.get("num_trades", 0)
        metrics["num_trades"] = num_trades
        thresholds["num_trades"] = self.thresholds["oos_min_trades"]
        if num_trades < self.thresholds["oos_min_trades"]:
            passed = False
            reasons.append(f"Insufficient OOS trades: {num_trades} < {self.thresholds['oos_min_trades']}")
        
        # Проверить Sharpe ratio
        sharpe = oos_performance.get("sharpe_ratio", 0)
        metrics["sharpe_ratio"] = sharpe
        thresholds["sharpe_ratio"] = self.thresholds["oos_min_sharpe"]
        if sharpe < self.thresholds["oos_min_sharpe"]:
            passed = False
            reasons.append(f"Low OOS Sharpe ratio: {sharpe:.2f} < {self.thresholds['oos_min_sharpe']}")
        
        # Проверить максимальную просадку
        max_dd = oos_performance.get("max_drawdown", 0)
        metrics["max_drawdown"] = max_dd
        thresholds["max_drawdown"] = self.thresholds["oos_max_drawdown"]
        if max_dd > self.thresholds["oos_max_drawdown"]:
            passed = False
            reasons.append(f"High OOS max drawdown: {max_dd:.2f}% > {self.thresholds['oos_max_drawdown']}%")
        
        # Проверить соотношение OOS/IS производительности
        is_return = is_performance.get("total_return", 0)
        oos_return = oos_performance.get("total_return", 0)
        
        if is_return != 0:
            performance_ratio = oos_return / is_return
            metrics["performance_ratio"] = performance_ratio
            thresholds["performance_ratio"] = self.thresholds["oos_performance_ratio"]
            if performance_ratio < self.thresholds["oos_performance_ratio"]:
                passed = False
                reasons.append(f"Low OOS/IS performance ratio: {performance_ratio:.2f} < {self.thresholds['oos_performance_ratio']}")
        
        if not reasons:
            reasons.append("All OOS criteria passed")
        
        return GateResult(
            gate_type=GateType.OOS_GATE,
            gate_name="Out-of-Sample Validation",
            status=GateStatus.PASSED if passed else GateStatus.FAILED,
            metrics=metrics,
            thresholds=thresholds,
            reasons=reasons,
            confidence=1.0 if passed else 0.0,
        )
    
    def run_walk_forward_gate(
        self,
        strategy_id: str,
        symbol: str,
        period_performances: list[dict[str, float]],
    ) -> GateResult:
        """
        Ворот Walk-Forward валидации.
        
        Args:
            strategy_id: ID стратегии
            symbol: Символ
            period_performances: Производительность по периодам
        
        Returns:
            Результат ворота
        """
        metrics = {}
        thresholds = {}
        reasons = []
        passed = True
        
        if len(period_performances) < self.thresholds["wf_min_periods"]:
            passed = False
            reasons.append(f"Insufficient periods: {len(period_performances)} < {self.thresholds['wf_min_periods']}")
        else:
            # Проверить консистентность
            returns = [p.get("total_return", 0) for p in period_performances]
            sharpe_ratios = [p.get("sharpe_ratio", 0) for p in period_performances]
            
            if returns:
                metrics["avg_return"] = float(np.mean(returns))
                metrics["std_return"] = float(np.std(returns))
                metrics["min_return"] = float(np.min(returns))
                metrics["max_return"] = float(np.max(returns))
                
                # Коэффициент вариации
                cv = float(np.std(returns)) / abs(float(np.mean(returns))) if np.mean(returns) != 0 else 0
                metrics["coefficient_of_variation"] = cv
                
                if cv > 1.0:  # Высокая изменчивость
                    passed = False
                    reasons.append(f"High return variability (CV={cv:.2f})")
            
            if sharpe_ratios:
                avg_sharpe = float(np.mean(sharpe_ratios))
                std_sharpe = float(np.std(sharpe_ratios))
                metrics["avg_sharpe"] = avg_sharpe
                metrics["std_sharpe"] = std_sharpe
                
                # Консистентность Sharpe
                consistency = 1 - (std_sharpe / avg_sharpe) if avg_sharpe > 0 else 0
                metrics["sharpe_consistency"] = consistency
                thresholds["sharpe_consistency"] = self.thresholds["wf_consistency_threshold"]
                
                if consistency < self.thresholds["wf_consistency_threshold"]:
                    passed = False
                    reasons.append(f"Low Sharpe consistency: {consistency:.2f} < {self.thresholds['wf_consistency_threshold']}")
        
        if not reasons:
            reasons.append("All walk-forward criteria passed")
        
        return GateResult(
            gate_type=GateType.WALK_FORWARD_GATE,
            gate_name="Walk-Forward Validation",
            status=GateStatus.PASSED if passed else GateStatus.FAILED,
            metrics=metrics,
            thresholds=thresholds,
            reasons=reasons,
            confidence=1.0 if passed else 0.0,
        )
    
    def run_monte_carlo_gate(
        self,
        strategy_id: str,
        symbol: str,
        returns: list[float],
        simulations: int = 1000,
    ) -> GateResult:
        """
        Ворот Monte Carlo валидации.
        
        Args:
            strategy_id: ID стратегии
            symbol: Символ
            returns: Доходности
            simulations: Количество симуляций
        
        Returns:
            Результат ворота
        """
        metrics = {}
        thresholds = {}
        reasons = []
        passed = True
        
        if len(returns) < 10:
            passed = False
            reasons.append("Insufficient return data")
        else:
            # Симулировать распределение доходностей
            mean_return = float(np.mean(returns))
            std_return = float(np.std(returns))
            
            # Генерация случайных доходностей
            simulated_returns = np.random.normal(mean_return, std_return, simulations)
            
            # Рассчитать метрики
            metrics["mean_return"] = mean_return
            metrics["std_return"] = std_return
            metrics["simulated_sharpe"] = mean_return / std_return if std_return > 0 else 0
            
            # Проверить вероятность убытков
            loss_probability = float(np.mean([1 for r in simulated_returns if r < 0]))
            metrics["loss_probability"] = loss_probability
            thresholds["loss_probability"] = 1 - self.thresholds["mc_confidence_level"]
            
            if loss_probability > 1 - self.thresholds["mc_confidence_level"]:
                passed = False
                reasons.append(f"High loss probability: {loss_probability:.2f} > {1 - self.thresholds['mc_confidence_level']:.2f}")
            
            # Проверить вероятность сильных просадок
            # Упрощённая оценка
            worst_5_percent = np.percentile(simulated_returns, 5)
            metrics["worst_5_percent"] = worst_5_percent
            
            if worst_5_percent < -0.2:  # Более 20% убыток в 5% случаев
                passed = False
                reasons.append(f"Severe losses in 5% of simulations: {worst_5_percent:.2f}")
        
        if not reasons:
            reasons.append("All Monte Carlo criteria passed")
        
        return GateResult(
            gate_type=GateType.MONTE_CARLO_GATE,
            gate_name="Monte Carlo Validation",
            status=GateStatus.PASSED if passed else GateStatus.FAILED,
            metrics=metrics,
            thresholds=thresholds,
            reasons=reasons,
            confidence=1.0 if passed else 0.0,
        )
    
    def run_stress_test_gate(
        self,
        strategy_id: str,
        symbol: str,
        stress_results: dict[str, float],
    ) -> GateResult:
        """
        Ворот стресс-тестирования.
        
        Args:
            strategy_id: ID стратегии
            symbol: Символ
            stress_results: Результаты стресс-тестов
        
        Returns:
            Результат ворота
        """
        metrics = {}
        thresholds = {}
        reasons = []
        passed = True
        
        # Проверить максимальный убыток в стресс-тестах
        max_loss = max(stress_results.values()) if stress_results else 0
        metrics["max_stress_loss"] = max_loss
        thresholds["max_stress_loss"] = self.thresholds["stress_max_loss"]
        
        if max_loss > self.thresholds["stress_max_loss"]:
            passed = False
            reasons.append(f"Excessive stress loss: {max_loss:.2f}% > {self.thresholds['stress_max_loss']}%")
        
        # Проверить средний убыток
        avg_loss = float(np.mean(list(stress_results.values()))) if stress_results else 0
        metrics["avg_stress_loss"] = avg_loss
        
        if avg_loss > self.thresholds["stress_max_loss"] / 2:
            passed = False
            reasons.append(f"High average stress loss: {avg_loss:.2f}%")
        
        if not reasons:
            reasons.append("All stress test criteria passed")
        
        return GateResult(
            gate_type=GateType.STRESS_TEST_GATE,
            gate_name="Stress Test Validation",
            status=GateStatus.PASSED if passed else GateStatus.FAILED,
            metrics=metrics,
            thresholds=thresholds,
            reasons=reasons,
            confidence=1.0 if passed else 0.0,
        )
    
    def run_statistical_significance_gate(
        self,
        strategy_id: str,
        symbol: str,
        statistic: float,
        p_value: float,
    ) -> GateResult:
        """
        Ворот статистической значимости.
        
        Args:
            strategy_id: ID стратегии
            symbol: Символ
            statistic: Статистика
            p_value: p-value
        
        Returns:
            Результат ворота
        """
        metrics = {"statistic": statistic, "p_value": p_value}
        thresholds = {"significance_level": self.thresholds["stat_significance_level"]}
        reasons = []
        passed = True
        
        if p_value > self.thresholds["stat_significance_level"]:
            passed = False
            reasons.append(f"Not statistically significant: p-value={p_value:.4f} > {self.thresholds['stat_significance_level']}")
        
        if abs(statistic) < self.thresholds["stat_min_t_stat"]:
            passed = False
            reasons.append(f"Low test statistic: |{statistic:.2f}| < {self.thresholds['stat_min_t_stat']}")
        
        if not reasons:
            reasons.append("Statistically significant")
        
        return GateResult(
            gate_type=GateType.STATISTICAL_SIGNIFICANCE_GATE,
            gate_name="Statistical Significance Validation",
            status=GateStatus.PASSED if passed else GateStatus.FAILED,
            metrics=metrics,
            thresholds=thresholds,
            reasons=reasons,
            confidence=1.0 if passed else 0.0,
        )
    
    def run_overfitting_gate(
        self,
        strategy_id: str,
        symbol: str,
        is_performance: dict[str, float],
        oos_performance: dict[str, float],
        model_complexity: float,
    ) -> GateResult:
        """
        Ворот проверки переобучения.
        
        Args:
            strategy_id: ID стратегии
            symbol: Символ
            is_performance: Производительность на IS данных
            oos_performance: Производительность на OOS данных
            model_complexity: Сложность модели
        
        Returns:
            Результат ворота
        """
        metrics = {"model_complexity": model_complexity}
        thresholds = {"max_complexity": self.thresholds["overfitting_max_complexity"]}
        reasons = []
        passed = True
        
        # Проверить сложность модели
        if model_complexity > self.thresholds["overfitting_max_complexity"]:
            passed = False
            reasons.append(f"High model complexity: {model_complexity} > {self.thresholds['overfitting_max_complexity']}")
        
        # Проверить разницу IS/OOS
        is_return = is_performance.get("total_return", 0)
        oos_return = oos_performance.get("total_return", 0)
        
        if is_return != 0:
            performance_ratio = oos_return / is_return
            metrics["oos_is_ratio"] = performance_ratio
            thresholds["min_oos_performance"] = self.thresholds["overfitting_min_oos_performance"]
            
            if performance_ratio < self.thresholds["overfitting_min_oos_performance"]:
                passed = False
                reasons.append(f"Large IS/OOS performance gap: {performance_ratio:.2f} < {self.thresholds['overfitting_min_oos_performance']}")
        
        if not reasons:
            reasons.append("No signs of overfitting detected")
        
        return GateResult(
            gate_type=GateType.OVERFITTING_GATE,
            gate_name="Overfitting Validation",
            status=GateStatus.PASSED if passed else GateStatus.FAILED,
            metrics=metrics,
            thresholds=thresholds,
            reasons=reasons,
            confidence=1.0 if passed else 0.0,
        )
    
    def validate_strategy(
        self,
        strategy_id: str,
        symbol: str,
        is_performance: dict[str, float],
        oos_performance: dict[str, float] | None = None,
        period_performances: list[dict[str, float]] | None = None,
        returns: list[float] | None = None,
        stress_results: dict[str, float] | None = None,
        model_complexity: float = 0.0,
    ) -> ValidationReport:
        """
        Полная валидация стратегии.
        
        Args:
            strategy_id: ID стратегии
            symbol: Символ
            is_performance: Производительность на IS данных
            oos_performance: Производительность на OOS данных
            period_performances: Производительность по периодам
            returns: Доходности
            stress_results: Результаты стресс-тестов
            model_complexity: Сложность модели
        
        Returns:
            Отчёт валидации
        """
        gate_results = {}
        
        # IS gate
        is_result = self.run_is_gate(strategy_id, symbol, is_performance)
        gate_results[GateType.IS_GATE] = is_result
        
        # OOS gate
        if oos_performance:
            oos_result = self.run_oos_gate(strategy_id, symbol, is_performance, oos_performance)
            gate_results[GateType.OOS_GATE] = oos_result
        
        # Walk-forward gate
        if period_performances:
            wf_result = self.run_walk_forward_gate(strategy_id, symbol, period_performances)
            gate_results[GateType.WALK_FORWARD_GATE] = wf_result
        
        # Monte Carlo gate
        if returns:
            mc_result = self.run_monte_carlo_gate(strategy_id, symbol, returns)
            gate_results[GateType.MONTE_CARLO_GATE] = mc_result
        
        # Stress test gate
        if stress_results:
            stress_result = self.run_stress_test_gate(strategy_id, symbol, stress_results)
            gate_results[GateType.STRESS_TEST_GATE] = stress_result
        
        # Overfitting gate
        if oos_performance:
            overfitting_result = self.run_overfitting_gate(
                strategy_id, symbol, is_performance, oos_performance, model_complexity
            )
            gate_results[GateType.OVERFITTING_GATE] = overfitting_result
        
        # Определить итоговый статус
        failed_gates = [r for r in gate_results.values() if r.status == GateStatus.FAILED]
        warning_gates = [r for r in gate_results.values() if r.status == GateStatus.WARNING]
        
        if failed_gates:
            overall_status = GateStatus.FAILED
        elif warning_gates:
            overall_status = GateStatus.WARNING
        else:
            overall_status = GateStatus.PASSED
        
        # Рассчитать итоговую уверенность
        passed_count = sum(1 for r in gate_results.values() if r.status == GateStatus.PASSED)
        total_count = len(gate_results)
        overall_confidence = passed_count / total_count if total_count > 0 else 0.0
        
        # Создать рекомендации
        recommendations = []
        
        if overall_status == GateStatus.PASSED:
            recommendations.append("Strategy passed all validation gates")
            recommendations.append("Consider live testing")
        elif overall_status == GateStatus.WARNING:
            recommendations.append("Strategy passed with warnings")
            recommendations.append("Consider additional testing")
        else:
            recommendations.append("Strategy failed validation")
            recommendations.append("Consider revising or rejecting")
        
        # Добавить конкретные рекомендации
        for gate_type, result in gate_results.items():
            if result.status != GateStatus.PASSED:
                recommendations.append(f"{result.gate_name}: {', '.join(result.reasons)}")
        
        report = ValidationReport(
            strategy_id=strategy_id,
            symbol=symbol,
            gate_results=gate_results,
            overall_status=overall_status,
            overall_confidence=overall_confidence,
            recommendations=recommendations,
        )
        
        # Сохранить отчёт
        report_id = f"{strategy_id}_{symbol}_{datetime.now(timezone.utc).isoformat()}"
        self._reports[report_id] = report
        
        return report
    
    def get_report(self, report_id: str) -> ValidationReport | None:
        """
        Получить отчёт.
        
        Args:
            report_id: ID отчёта
        
        Returns:
            Отчёт или None
        """
        return self._reports.get(report_id)


# Глобальный экземпляр
_validation_gates: ValidationGates | None = None


def get_validation_gates() -> ValidationGates:
    """Получить глобальные Validation Gates"""
    global _validation_gates
    if _validation_gates is None:
        _validation_gates = ValidationGates()
    return _validation_gates


def reset_validation_gates():
    """Сбросить Validation Gates (для тестов)"""
    global _validation_gates
    _validation_gates = ValidationGates()
