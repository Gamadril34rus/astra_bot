"""
ASTRA BOT — Counterfactual Engine

Движок контрфактного анализа (Master Specification v2, Section 21)

После закрытия сделки рассчитывает:
- Что было бы, если вход произошёл через +1 минуту
- Что было бы, если вход произошёл через -1 минуту
- Что было бы, если position size был меньше
- Что было бы, если exit произошёл раньше
- Что было бы, если exit произошёл позже
- Что было бы, если использовался другой stop
- Что было бы, если использовался другой execution method

Сохраняет:
- actual_result
- counterfactual_results
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TradeOutcome:
    """Результат сделки"""
    pnl: float
    return_pct: float
    win: bool
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "pnl": self.pnl,
            "return_pct": self.return_pct,
            "win": self.win,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "entry_time": self.entry_time.isoformat(),
            "exit_time": self.exit_time.isoformat(),
        }


@dataclass
class CounterfactualScenario:
    """Контрфактный сценарий"""
    description: str
    actual_outcome: TradeOutcome
    counterfactual_outcome: TradeOutcome
    difference: float  # Разница в PnL
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "actual_outcome": self.actual_outcome.to_dict(),
            "counterfactual_outcome": self.counterfactual_outcome.to_dict(),
            "difference": self.difference,
        }


@dataclass
class CounterfactualResult:
    """Результат контрфактного анализа"""
    trade_id: str
    symbol: str
    actual_result: TradeOutcome
    counterfactual_scenarios: list[CounterfactualScenario] = field(default_factory=list)
    
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "actual_result": self.actual_result.to_dict(),
            "counterfactual_scenarios": [s.to_dict() for s in self.counterfactual_scenarios],
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class PriceHistory:
    """История цен"""
    symbol: str
    prices: list[tuple[datetime, float]]  # (timestamp, price)
    
    def get_price_at(self, timestamp: datetime) -> float | None:
        """Получить цену в указанный момент времени"""
        for ts, price in self.prices:
            if ts == timestamp:
                return price
        return None
    
    def get_price_around(self, timestamp: datetime, delta: timedelta) -> float | None:
        """Получить цену около указанного момента времени"""
        for ts, price in self.prices:
            if abs((ts - timestamp).total_seconds()) <= delta.total_seconds():
                return price
        return None


class CounterfactualEngine:
    """
    Движок контрфактного анализа.
    
    Рассчитывает альтернативные исходы сделок для обучения системы.
    """
    
    def __init__(self):
        # Хранение истории цен
        self.price_histories: dict[str, PriceHistory] = {}
        
        # Хранение результатов
        self.results: dict[str, CounterfactualResult] = {}
    
    def add_price_point(self, symbol: str, timestamp: datetime, price: float) -> None:
        """
        Добавить точку цены в историю.
        
        Args:
            symbol: Символ инструмента
            timestamp: Временная метка
            price: Цена
        """
        if symbol not in self.price_histories:
            self.price_histories[symbol] = PriceHistory(
                symbol=symbol,
                prices=[]
            )
        
        self.price_histories[symbol].prices.append((timestamp, price))
        
        # Сортировать по времени
        self.price_histories[symbol].prices.sort(key=lambda x: x[0])
    
    def simulate_delayed_entry(
        self,
        actual_entry_time: datetime,
        actual_entry_price: float,
        exit_time: datetime,
        exit_price: float,
        symbol: str,
        delay: timedelta
    ) -> TradeOutcome | None:
        """
        Симулировать вход с задержкой.
        
        Args:
            actual_entry_time: Фактическое время входа
            actual_entry_price: Фактическая цена входа
            exit_time: Время выхода
            exit_price: Цена выхода
            symbol: Символ инструмента
            delay: Задержка
        
        Returns:
            Результат сделки с задержкой входа
        """
        if symbol not in self.price_histories:
            return None
        
        # Найти цену через delay после фактического входа
        delayed_entry_time = actual_entry_time + delay
        delayed_entry_price = self.price_histories[symbol].get_price_around(
            delayed_entry_time, timedelta(minutes=1)
        )
        
        if delayed_entry_price is None:
            return None
        
        # Рассчитать PnL
        pnl = exit_price - delayed_entry_price
        return_pct = (pnl / delayed_entry_price) * 100 if delayed_entry_price > 0 else 0
        
        return TradeOutcome(
            pnl=pnl,
            return_pct=return_pct,
            win=pnl > 0,
            entry_price=delayed_entry_price,
            exit_price=exit_price,
            entry_time=delayed_entry_time,
            exit_time=exit_time
        )
    
    def simulate_early_entry(
        self,
        actual_entry_time: datetime,
        actual_entry_price: float,
        exit_time: datetime,
        exit_price: float,
        symbol: str,
        advance: timedelta
    ) -> TradeOutcome | None:
        """
        Симулировать вход раньше времени.
        
        Args:
            actual_entry_time: Фактическое время входа
            actual_entry_price: Фактическая цена входа
            exit_time: Время выхода
            exit_price: Цена выхода
            symbol: Символ инструмента
            advance: Опережение
        
        Returns:
            Результат сделки с ранним входом
        """
        if symbol not in self.price_histories:
            return None
        
        # Найти цену до фактического входа
        early_entry_time = actual_entry_time - advance
        early_entry_price = self.price_histories[symbol].get_price_around(
            early_entry_time, timedelta(minutes=1)
        )
        
        if early_entry_price is None:
            return None
        
        # Рассчитать PnL
        pnl = exit_price - early_entry_price
        return_pct = (pnl / early_entry_price) * 100 if early_entry_price > 0 else 0
        
        return TradeOutcome(
            pnl=pnl,
            return_pct=return_pct,
            win=pnl > 0,
            entry_price=early_entry_price,
            exit_price=exit_price,
            entry_time=early_entry_time,
            exit_time=exit_time
        )
    
    def simulate_smaller_position(
        self,
        actual_outcome: TradeOutcome,
        size_reduction: float = 0.5
    ) -> TradeOutcome:
        """
        Симулировать меньший размер позиции.
        
        Args:
            actual_outcome: Фактический результат
            size_reduction: Уменьшение размера (0-1)
        
        Returns:
            Результат с меньшим размером
        """
        reduced_pnl = actual_outcome.pnl * size_reduction
        
        return TradeOutcome(
            pnl=reduced_pnl,
            return_pct=actual_outcome.return_pct * size_reduction,
            win=reduced_pnl > 0,
            entry_price=actual_outcome.entry_price,
            exit_price=actual_outcome.exit_price,
            entry_time=actual_outcome.entry_time,
            exit_time=actual_outcome.exit_time
        )
    
    def simulate_early_exit(
        self,
        actual_entry_time: datetime,
        actual_entry_price: float,
        actual_exit_time: datetime,
        actual_exit_price: float,
        symbol: str,
        advance: timedelta
    ) -> TradeOutcome | None:
        """
        Симулировать ранний выход.
        
        Args:
            actual_entry_time: Фактическое время входа
            actual_entry_price: Фактическая цена входа
            actual_exit_time: Фактическое время выхода
            actual_exit_price: Фактическая цена выхода
            symbol: Символ инструмента
            advance: Опережение
        
        Returns:
            Результат сделки с ранним выходом
        """
        if symbol not in self.price_histories:
            return None
        
        # Найти цену до фактического выхода
        early_exit_time = actual_exit_time - advance
        early_exit_price = self.price_histories[symbol].get_price_around(
            early_exit_time, timedelta(minutes=1)
        )
        
        if early_exit_price is None:
            return None
        
        # Рассчитать PnL
        pnl = early_exit_price - actual_entry_price
        return_pct = (pnl / actual_entry_price) * 100 if actual_entry_price > 0 else 0
        
        return TradeOutcome(
            pnl=pnl,
            return_pct=return_pct,
            win=pnl > 0,
            entry_price=actual_entry_price,
            exit_price=early_exit_price,
            entry_time=actual_entry_time,
            exit_time=early_exit_time
        )
    
    def simulate_late_exit(
        self,
        actual_entry_time: datetime,
        actual_entry_price: float,
        actual_exit_time: datetime,
        actual_exit_price: float,
        symbol: str,
        delay: timedelta
    ) -> TradeOutcome | None:
        """
        Симулировать поздний выход.
        
        Args:
            actual_entry_time: Фактическое время входа
            actual_entry_price: Фактическая цена входа
            actual_exit_time: Фактическое время выхода
            actual_exit_price: Фактическая цена выхода
            symbol: Символ инструмента
            delay: Задержка
        
        Returns:
            Результат сделки с поздним выходом
        """
        if symbol not in self.price_histories:
            return None
        
        # Найти цену после фактического выхода
        late_exit_time = actual_exit_time + delay
        late_exit_price = self.price_histories[symbol].get_price_around(
            late_exit_time, timedelta(minutes=1)
        )
        
        if late_exit_price is None:
            return None
        
        # Рассчитать PnL
        pnl = late_exit_price - actual_entry_price
        return_pct = (pnl / actual_entry_price) * 100 if actual_entry_price > 0 else 0
        
        return TradeOutcome(
            pnl=pnl,
            return_pct=return_pct,
            win=pnl > 0,
            entry_price=actual_entry_price,
            exit_price=late_exit_price,
            entry_time=actual_entry_time,
            exit_time=late_exit_time
        )
    
    def simulate_different_stop(
        self,
        actual_entry_price: float,
        actual_stop_price: float,
        actual_exit_price: float,
        new_stop_price: float
    ) -> TradeOutcome:
        """
        Симулировать другой стоп.
        
        Args:
            actual_entry_price: Фактическая цена входа
            actual_stop_price: Фактическая цена стопа
            actual_exit_price: Фактическая цена выхода
            new_stop_price: Новая цена стопа
        
        Returns:
            Результат сделки с другим стопом
        """
        # Если цена дошла до нового стопа
        if (actual_entry_price > new_stop_price and actual_exit_price <= new_stop_price) or \
           (actual_entry_price < new_stop_price and actual_exit_price >= new_stop_price):
            # Выход по новому стопу
            pnl = new_stop_price - actual_entry_price
            return_pct = (pnl / actual_entry_price) * 100 if actual_entry_price > 0 else 0
            
            return TradeOutcome(
                pnl=pnl,
                return_pct=return_pct,
                win=pnl > 0,
                entry_price=actual_entry_price,
                exit_price=new_stop_price,
                entry_time=datetime.now(),
                exit_time=datetime.now()
            )
        else:
            # Выход по фактической цене
            pnl = actual_exit_price - actual_entry_price
            return_pct = (pnl / actual_entry_price) * 100 if actual_entry_price > 0 else 0
            
            return TradeOutcome(
                pnl=pnl,
                return_pct=return_pct,
                win=pnl > 0,
                entry_price=actual_entry_price,
                exit_price=actual_exit_price,
                entry_time=datetime.now(),
                exit_time=datetime.now()
            )
    
    def analyze_trade(
        self,
        trade_id: str,
        symbol: str,
        actual_outcome: TradeOutcome,
        stop_price: float | None = None,
        target_price: float | None = None
    ) -> CounterfactualResult:
        """
        Провести полный контрфактный анализ сделки.
        
        Args:
            trade_id: Идентификатор сделки
            symbol: Символ инструмента
            actual_outcome: Фактический результат
            stop_price: Цена стопа
            target_price: Цена тейк-профита
        
        Returns:
            CounterfactualResult
        """
        scenarios = []
        
        # 1. Вход через +1 минуту
        delayed_1m = self.simulate_delayed_entry(
            actual_outcome.entry_time,
            actual_outcome.entry_price,
            actual_outcome.exit_time,
            actual_outcome.exit_price,
            symbol,
            timedelta(minutes=1)
        )
        if delayed_1m:
            scenarios.append(CounterfactualScenario(
                description="Entry delayed by +1 minute",
                actual_outcome=actual_outcome,
                counterfactual_outcome=delayed_1m,
                difference=delayed_1m.pnl - actual_outcome.pnl
            ))
        
        # 2. Вход через -1 минуту
        early_1m = self.simulate_early_entry(
            actual_outcome.entry_time,
            actual_outcome.entry_price,
            actual_outcome.exit_time,
            actual_outcome.exit_price,
            symbol,
            timedelta(minutes=1)
        )
        if early_1m:
            scenarios.append(CounterfactualScenario(
                description="Entry advanced by -1 minute",
                actual_outcome=actual_outcome,
                counterfactual_outcome=early_1m,
                difference=early_1m.pnl - actual_outcome.pnl
            ))
        
        # 3. Размер позиции меньше на 50%
        smaller_position = self.simulate_smaller_position(
            actual_outcome,
            size_reduction=0.5
        )
        scenarios.append(CounterfactualScenario(
            description="Position size reduced by 50%",
            actual_outcome=actual_outcome,
            counterfactual_outcome=smaller_position,
            difference=smaller_position.pnl - actual_outcome.pnl
        ))
        
        # 4. Выход раньше на 5 минут
        early_exit = self.simulate_early_exit(
            actual_outcome.entry_time,
            actual_outcome.entry_price,
            actual_outcome.exit_time,
            actual_outcome.exit_price,
            symbol,
            timedelta(minutes=5)
        )
        if early_exit:
            scenarios.append(CounterfactualScenario(
                description="Exit advanced by -5 minutes",
                actual_outcome=actual_outcome,
                counterfactual_outcome=early_exit,
                difference=early_exit.pnl - actual_outcome.pnl
            ))
        
        # 5. Выход позже на 5 минут
        late_exit = self.simulate_late_exit(
            actual_outcome.entry_time,
            actual_outcome.entry_price,
            actual_outcome.exit_time,
            actual_outcome.exit_price,
            symbol,
            timedelta(minutes=5)
        )
        if late_exit:
            scenarios.append(CounterfactualScenario(
                description="Exit delayed by +5 minutes",
                actual_outcome=actual_outcome,
                counterfactual_outcome=late_exit,
                difference=late_exit.pnl - actual_outcome.pnl
            ))
        
        # 6. Другой стоп (если есть стоп)
        if stop_price is not None:
            # Новый стоп на 10% ближе к цене входа
            if actual_outcome.entry_price > stop_price:
                new_stop = actual_outcome.entry_price - (
                    actual_outcome.entry_price - stop_price
                ) * 0.9
            else:
                new_stop = actual_outcome.entry_price + (
                    stop_price - actual_outcome.entry_price
                ) * 0.9
            
            different_stop = self.simulate_different_stop(
                actual_outcome.entry_price,
                stop_price,
                actual_outcome.exit_price,
                new_stop
            )
            scenarios.append(CounterfactualScenario(
                description="Different stop loss (10% closer)",
                actual_outcome=actual_outcome,
                counterfactual_outcome=different_stop,
                difference=different_stop.pnl - actual_outcome.pnl
            ))
        
        # 7. Другой метод исполнения (рыночный vs лимитный)
        # Для простоты симулируем небольшое улучшение/ухудшение цены
        market_outcome = TradeOutcome(
            pnl=actual_outcome.pnl * 0.99,  # Небольшое ухудшение
            return_pct=actual_outcome.return_pct * 0.99,
            win=actual_outcome.win,
            entry_price=actual_outcome.entry_price,
            exit_price=actual_outcome.exit_price,
            entry_time=actual_outcome.entry_time,
            exit_time=actual_outcome.exit_time
        )
        scenarios.append(CounterfactualScenario(
            description="Market order instead of limit",
            actual_outcome=actual_outcome,
            counterfactual_outcome=market_outcome,
            difference=market_outcome.pnl - actual_outcome.pnl
        ))
        
        result = CounterfactualResult(
            trade_id=trade_id,
            symbol=symbol,
            actual_result=actual_outcome,
            counterfactual_scenarios=scenarios
        )
        
        self.results[trade_id] = result
        
        return result
    
    def get_best_counterfactual(
        self,
        result: CounterfactualResult
    ) -> CounterfactualScenario | None:
        """
        Получить лучший контрфактный сценарий.
        
        Args:
            result: Результат контрфактного анализа
        
        Returns:
            Лучший сценарий или None
        """
        if not result.counterfactual_scenarios:
            return None
        
        # Лучший сценарий - тот, который даёт максимальный PnL
        best_scenario = max(
            result.counterfactual_scenarios,
            key=lambda x: x.counterfactual_outcome.pnl
        )
        
        return best_scenario
    
    def get_worst_counterfactual(
        self,
        result: CounterfactualResult
    ) -> CounterfactualScenario | None:
        """
        Получить худший контрфактный сценарий.
        
        Args:
            result: Результат контрфактного анализа
        
        Returns:
            Худший сценарий или None
        """
        if not result.counterfactual_scenarios:
            return None
        
        # Худший сценарий - тот, который даёт минимальный PnL
        worst_scenario = min(
            result.counterfactual_scenarios,
            key=lambda x: x.counterfactual_outcome.pnl
        )
        
        return worst_scenario
    
    def get_opportunity_cost(
        self,
        result: CounterfactualResult
    ) -> float:
        """
        Рассчитать opportunity cost (Section 22, 57).
        
        Args:
            result: Результат контрфактного анализа
        
        Returns:
            Opportunity cost
        """
        best_scenario = self.get_best_counterfactual(result)
        
        if best_scenario is None:
            return 0.0
        
        # Opportunity cost = разница между лучшим контрфактным и фактическим
        return best_scenario.difference
    
    def get_regret(
        self,
        result: CounterfactualResult
    ) -> float:
        """
        Рассчитать regret (сожаление).
        
        Args:
            result: Результат контрфактного анализа
        
        Returns:
            Regret
        """
        worst_scenario = self.get_worst_counterfactual(result)
        
        if worst_scenario is None:
            return 0.0
        
        # Regret = разница между фактическим и худшим контрфактным
        return actual_outcome.pnl - worst_scenario.counterfactual_outcome.pnl
    
    def cleanup_old_results(self, max_age_days: int = 30) -> int:
        """
        Очистить старые результаты.
        
        Args:
            max_age_days: Максимальный возраст результатов в днях
        
        Returns:
            Количество удалённых результатов
        """
        cutoff = datetime.now() - timedelta(days=max_age_days)
        
        results_to_remove = [
            key for key, result in self.results.items()
            if result.timestamp < cutoff
        ]
        
        for key in results_to_remove:
            del self.results[key]
        
        return len(results_to_remove)


# Глобальный экземпляр Counterfactual Engine
_counterfactual_engine: CounterfactualEngine | None = None


def get_counterfactual_engine() -> CounterfactualEngine:
    """Получить глобальный Counterfactual Engine"""
    global _counterfactual_engine
    if _counterfactual_engine is None:
        _counterfactual_engine = CounterfactualEngine()
    return _counterfactual_engine


def reset_counterfactual_engine():
    """Сбросить Counterfactual Engine (для тестов)"""
    global _counterfactual_engine
    _counterfactual_engine = CounterfactualEngine()
