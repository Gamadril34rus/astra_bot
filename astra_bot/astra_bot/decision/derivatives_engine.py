"""Derivatives engine — стаб для funding/OI/ликвидаций."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DerivativesReport:
    funding: float = 0.0
    open_interest_change: float = 0.0
    long_short_ratio: float = 1.0
    liquidations_24h: float = 0.0
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return self.__dict__


class DerivativesEngine:
    def assess(
        self,
        symbol: str,
        *,
        funding: float = 0.0,
        open_interest_change: float = 0.0,
        long_short_ratio: float = 1.0,
        liquidations_24h: float = 0.0,
    ) -> DerivativesReport:
        return DerivativesReport(
            funding=funding,
            open_interest_change=open_interest_change,
            long_short_ratio=long_short_ratio,
            liquidations_24h=liquidations_24h,
        )
