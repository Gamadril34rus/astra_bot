"""Единый реестр торговых «мозгов» и исследовательской памяти ASTRA.

Централизованно управляет статусами, источниками и разграничением прав на
исполнение (execution permission) для всех формализованных стратегий и
исследовательских концепций.

Принцип безопасности: **fail-closed**. Ни одна стратегия не имеет права
открывать исполняемые ордера без явного присвоения статуса TIER_CHAMPION
после всех прохождений аудта. По умолчанию `execution_strategies()` возвращает [].
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Tiers / Статусы готовности стратегий
TIER_AUDIT = "audit"          # Формализована, подлежит бэктест-аудиту
TIER_RESEARCH = "research"    # Концепция/гипотеза из либермора/сороса, без прямого кодинга сигналов
TIER_CHAMPION = "champion"    # Полностью валидирована на OOS/walk-forward, разрешена к исполнению

@dataclass
class StrategyRegistryEntry:
    key: str
    name: str
    source: str
    tier: str
    execution_blocked_reason: str
    factory: Callable[[], Any] | None = None


# Реестр всех известных мозгов
STRATEGY_REGISTRY: dict[str, StrategyRegistryEntry] = {
    "book_breakout": StrategyRegistryEntry(
        key="book_breakout",
        name="Book Breakout & Retest",
        source="Simple Trading Book",
        tier=TIER_AUDIT,
        execution_blocked_reason="Находится на этапе многолетнего бэктест-аудита.",
    ),
    "momentum": StrategyRegistryEntry(
        key="momentum",
        name="Momentum Breakout",
        source="Astra Core",
        tier=TIER_AUDIT,
        execution_blocked_reason="Находится на этапе многолетнего бэктест-аудита.",
    ),
    "mean_reversion": StrategyRegistryEntry(
        key="mean_reversion",
        name="Mean Reversion (RSI/BB)",
        source="Astra Core",
        tier=TIER_AUDIT,
        execution_blocked_reason="Находится на этапе многолетнего бэктест-аудита.",
    ),
    "pullback": StrategyRegistryEntry(
        key="pullback",
        name="EMA Pullback in Trend",
        source="Astra Core",
        tier=TIER_AUDIT,
        execution_blocked_reason="Находится на этапе многолетнего бэктест-аудита.",
    ),
    "high_winrate": StrategyRegistryEntry(
        key="high_winrate",
        name="High Winrate Conservative",
        source="Astra Core",
        tier=TIER_AUDIT,
        execution_blocked_reason="Находится на этапе многолетнего бэктест-аудита.",
    ),
    "selective": StrategyRegistryEntry(
        key="selective",
        name="Selective Filter",
        source="Astra Core",
        tier=TIER_AUDIT,
        execution_blocked_reason="Находится на этапе многолетнего бэктест-аудита.",
    ),
    "ts_momentum": StrategyRegistryEntry(
        key="ts_momentum",
        name="Time Series Momentum (45d)",
        source="Moskowitz et al.",
        tier=TIER_AUDIT,
        execution_blocked_reason="Находится на этапе многолетнего бэктест-аудита.",
    ),
    "ts_momentum_adx": StrategyRegistryEntry(
        key="ts_momentum_adx",
        name="Time Series Momentum + ADX",
        source="Moskowitz et al. + Wilder ADX",
        tier=TIER_AUDIT,
        execution_blocked_reason="Находится на этапе многолетнего бэктест-аудита.",
    ),
    "multicurrency_mtf": StrategyRegistryEntry(
        key="multicurrency_mtf",
        name="Multicurrency MTF Protocol",
        source="Мультивалютная торговая система",
        tier=TIER_AUDIT,
        execution_blocked_reason="Находится на этапе многолетнего бэктест-аудита.",
    ),
    # Research-only концепции (без прямого фабpичного исполнения)
    "livermore_pivot": StrategyRegistryEntry(
        key="livermore_pivot",
        name="Livermore Key Pivots & Confirmation",
        source="Jesse Livermore",
        tier=TIER_RESEARCH,
        execution_blocked_reason="Research-only: концептуальный макро-слой, не имеющий прямого фабричного кода.",
    ),
    "soros_regime": StrategyRegistryEntry(
        key="soros_regime",
        name="Soros Reflexivity & Information Shock",
        source="George Soros",
        tier=TIER_RESEARCH,
        execution_blocked_reason="Research-only: концептуальный макро-слой, не имеющий прямого фабричного кода.",
    ),
    "druckenmiller_driver": StrategyRegistryEntry(
        key="druckenmiller_driver",
        name="Druckenmiller Dominant Driver & Conviction",
        source="Stanley Druckenmiller",
        tier=TIER_RESEARCH,
        execution_blocked_reason="Research-only: концептуальный макро-слой, не имеющий прямого фабричного кода.",
    ),
    "tudor_risk": StrategyRegistryEntry(
        key="tudor_risk",
        name="Tudor Jones Macro Risk Control",
        source="Paul Tudor Jones",
        tier=TIER_RESEARCH,
        execution_blocked_reason="Research-only: концептуальный макро-слой, не имеющий прямого фабричного кода.",
    ),
}


def execution_strategies() -> list[Any]:
    """Возвращает список стратегий, разрешённых для виртуального/демо исполнения.

    Режим fail-closed: если стратегия не имеет статуса TIER_CHAMPION, она
    блокируется и возвращается пустой список [].
    """
    champions = []
    for entry in STRATEGY_REGISTRY.values():
        if entry.tier == TIER_CHAMPION and entry.factory is not None:
            champions.append(entry.factory())
    return champions
