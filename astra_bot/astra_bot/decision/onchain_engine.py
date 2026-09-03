"""On-chain engine — стаб.

Возвращает OnChainScore в диапазоне -1..1. Продакшн-реализация
тянет exchange flows, whale transfers, stablecoin supply и т.д.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OnChainReport:
    score: float = 0.0
    blocked: bool = False

    def to_dict(self) -> dict:
        return self.__dict__


class OnChainEngine:
    def assess(
        self,
        symbol: str,
        *,
        exchange_inflow: float = 0.0,
        exchange_outflow: float = 0.0,
        whale_transfers: float = 0.0,
    ) -> OnChainReport:
        # Положительный приток средств на биржи → медвежий сигнал.
        net = exchange_outflow - exchange_inflow + whale_transfers
        score = max(-1.0, min(1.0, net / 1_000_000.0))
        return OnChainReport(score=score, blocked=False)
