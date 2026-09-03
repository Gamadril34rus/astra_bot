"""Signal scoring — сборка балла из всех компонентов."""

from __future__ import annotations

from dataclasses import dataclass

from .config import DecisionConfig
from .context import SignalCandidate
from .correlation_engine import CorrelationReport
from .derivatives_engine import DerivativesReport
from .feature_engine import Features
from .news_engine import NewsReport
from .onchain_engine import OnChainReport
from .orderbook_engine import OrderBookReport
from .regime_engine import RegimeReport
from .structure_engine import StructureReport
from .technical_engine import TechnicalReport


@dataclass
class ComponentScores:
    trend: float = 0.0
    momentum: float = 0.0
    volume: float = 0.0
    structure: float = 0.0
    liquidity: float = 0.0
    order_book: float = 0.0
    news: float = 0.0
    onchain: float = 0.0
    derivatives: float = 0.0
    correlation: float = 0.0
    ml: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return self.__dict__


class SignalScorer:
    def __init__(self, config: DecisionConfig | None = None):
        self.config = config or DecisionConfig()

    def score(
        self,
        candidate: SignalCandidate,
        *,
        features: Features,
        regime: RegimeReport,
        technical: TechnicalReport,
        structure: StructureReport,
        book: OrderBookReport,
        news: NewsReport,
        onchain: OnChainReport,
        derivatives: DerivativesReport,
        correlation: CorrelationReport,
    ) -> ComponentScores:
        long = candidate.direction == "long"
        c = ComponentScores()

        # Trend: EMA alignment + ADX.
        if technical.trend == (1 if long else -1):
            c.trend = 18 if regime.confidence > 0.7 else 10
        elif technical.trend == 0:
            c.trend = 4
        else:
            c.trend = -8

        # Momentum.
        if (long and technical.momentum > 0.2) or (not long and technical.momentum < -0.2):
            c.momentum = 12
        elif technical.momentum == 0:
            c.momentum = 3

        # Volume.
        c.volume = 10 if technical.volume_confirmed else 2

        # Structure.
        if structure.pattern == ("HH_HL" if long else "LH_LL"):
            c.structure = 15
        elif (structure.breakout_long and long) or (structure.breakout_short and not long):
            c.structure = 12
        elif (structure.fakeout_long and long) or (structure.fakeout_short and not long):
            c.structure = -15
        else:
            c.structure = 4

        # Liquidity / Order book.
        c.liquidity = 10 if book.is_healthy and technical.atr_pct and technical.atr_pct < 4 else 2
        if book.imbalance > 0.2:
            c.order_book = 5 if long else -5
        elif book.imbalance < -0.2:
            c.order_book = -5 if long else 5
        else:
            c.order_book = 0

        # News: high score => risk-off.
        c.news = max(0, 8 - news.score / 10)
        if news.blocked:
            c.news = -20

        # On-chain/derivatives — небольшие добавки.
        c.onchain = 4 * (
            onchain.score if long else -onchain.score
        )
        c.onchain = max(-4, min(4, c.onchain))
        if derivatives.open_interest_change > 0.05:
            c.derivatives = 3
        elif derivatives.open_interest_change < -0.05:
            c.derivatives = -3

        # Correlation.
        c.correlation = 7 if not correlation.blocked else -20

        # ML: кандидат может иметь ml_probability.
        if candidate.ml_probability is not None:
            p = candidate.ml_probability
            # 0..15 баллов за вероятность выше порога.
            c.ml = max(0.0, min(15.0, (p - 0.5) * 30))
        else:
            c.ml = 4

        candidate.total_score = sum(c.as_dict().values())
        return c
