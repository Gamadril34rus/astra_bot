# ruff: noqa: UP042
"""Meta-Strategy: выбор стратегии по статистически подтверждённому EV.

Master prompt §8 / TZ §5: выбор не по ``total_score``, а по Expected Value
конкретной стратегии в текущем рыночном режиме с учётом sample size.

Правила:
1. Для каждого кандидата считается prior-EV в R на уровне сделки
   (P(win)×AvgWinR − P(loss)×AvgLossR − fees/slippage, в R).
2. Эмпирическое expectancy стратегии в текущем режиме (из
   ``StrategyStatsStore``) сжимается к prior:
   ``ev = w·sample + (1−w)·prior, w = n/(n+k)``.
3. Кандидат отклоняется, если:
   - ``ev < min_ev_r`` (LOW_EV) — включая отрицательный EV;
   - при достаточной выборке (n ≥ min_samples) и ``confidence < min_ev_confidence``
     (LOW_CONFIDENCE) — выборка «недостаточно убедительная» и EV на грани.
4. Выбор — кандидат с максимальным EV (при равенстве — больший score).
5. Если никто не прошёл — NO_TRADE с кодированной причиной (TZ §12).

Cold start (данных нет): ev = prior — поведение согласуется с прежним
гейтом ``min_expected_edge_pct``; при накоплении статистики оценка
становится режимно-зависимой автоматически.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .config import DecisionConfig
from .context import SignalCandidate
from .strategy_stats import StrategyStatsStore


class NoTradeReason(str, Enum):
    """Структурированные причины отказа (TZ §12)."""

    LOW_EV = "LOW_EV"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    BAD_REGIME = "BAD_REGIME"
    RISK_LIMIT = "RISK_LIMIT"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    SPREAD_TOO_HIGH = "SPREAD_TOO_HIGH"
    CORRELATED_EXPOSURE = "CORRELATED_EXPOSURE"
    HALT = "HALT"
    NO_VALID_SETUP = "NO_VALID_SETUP"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NEWS = "NEWS"
    INSUFFICIENT_TIME = "INSUFFICIENT_TIME"


# Маппинг legacy-причин пайплайна на кодированные (для backward compat
# в логах и уроках, пока не переделаны все места).
REASON_MAP: dict[str, NoTradeReason] = {
    "insufficient_data": NoTradeReason.INSUFFICIENT_DATA,
    "news_critical": NoTradeReason.NEWS,
    "extreme_volatility": NoTradeReason.HIGH_VOLATILITY,
    "unhealthy_orderbook": NoTradeReason.LOW_LIQUIDITY,
    "no_strategy_signal": NoTradeReason.NO_VALID_SETUP,
    "liquidity_too_thin": NoTradeReason.LOW_LIQUIDITY,
    "rr_too_low": NoTradeReason.LOW_EV,
    "trading_disabled": NoTradeReason.HALT,
    "daily_loss_limit": NoTradeReason.RISK_LIMIT,
    "weekly_loss_limit": NoTradeReason.RISK_LIMIT,
    "hard_drawdown": NoTradeReason.RISK_LIMIT,
}


def candidate_prior_r(
    candidate: SignalCandidate,
    fee_pct: float = 0.001,
    slippage_pct: float = 0.001,
) -> float:
    """Prior-EV кандидата в R на уровне сделки.

    P(win) берётся из ml_probability (если есть), иначе из confidence
    (консервативно: не ниже 0.4). Без тейка (flip-стратегии) ожидаемый
    выигрыш оценивается в 1R — консервативно, реальная оценка придёт из
    накопленной статистики.
    """
    entry = float(candidate.entry_price)
    stop = float(candidate.stop_loss)
    risk = abs(entry - stop)
    if entry <= 0 or risk <= 0:
        return 0.0
    take = float(candidate.take_profit) if candidate.take_profit and float(candidate.take_profit) > 0 else None
    avg_win_r = (abs(take - entry) / risk) if take else 1.0
    p_win = candidate.ml_probability if candidate.ml_probability is not None else max(0.4, float(candidate.confidence))
    p_win = min(0.99, max(0.01, p_win))
    stop_pct = risk / entry
    costs_r = (fee_pct + slippage_pct) / stop_pct  # издержки на две стороны, в R
    return p_win * avg_win_r - (1.0 - p_win) * 1.0 - costs_r


@dataclass
class CandidateEvaluation:
    """Оценка одного кандидата Meta-Strategy (для логов и наблюдений)."""

    strategy: str
    direction: str
    prior_r: float
    ev_r: float
    confidence: float
    sample_size: int
    total_score: float
    rejected: bool = False
    rejection: NoTradeReason | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "direction": self.direction,
            "prior_r": round(self.prior_r, 4),
            "ev_r": round(self.ev_r, 4),
            "confidence": round(self.confidence, 4),
            "sample_size": self.sample_size,
            "total_score": round(self.total_score, 2),
            "rejected": self.rejected,
            "rejection": self.rejection.value if self.rejection else None,
        }


@dataclass
class MetaDecision:
    chosen: SignalCandidate | None
    reason_code: NoTradeReason | None
    evaluations: list[CandidateEvaluation] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return self.reason_code.value if self.reason_code else "SELECTED"


class MetaStrategy:
    """Выбор кандидата по shrunken EV в текущем режиме (TZ §5)."""

    def __init__(self, stats: StrategyStatsStore, config: DecisionConfig | None = None) -> None:
        self.stats = stats
        self.config = config or DecisionConfig()

    @property
    def min_ev_r(self) -> float:
        return float(getattr(self.config, "min_ev_r", 0.0))

    @property
    def min_ev_confidence(self) -> float:
        return float(getattr(self.config, "min_ev_confidence", 0.3))

    def evaluate_candidate(
        self,
        candidate: SignalCandidate,
        regime: str,
        regime_axes: str | None = None,
    ) -> CandidateEvaluation:
        prior_r = candidate_prior_r(candidate)
        ev_r, confidence, bucket = self.stats.expectancy(
            strategy=candidate.strategy,
            regime=regime,
            timeframe=candidate.timeframe,
            prior_r=prior_r,
            regime_axes=regime_axes,
        )
        sample_size = bucket.sample_size if bucket else 0
        ev = CandidateEvaluation(
            strategy=candidate.strategy,
            direction=candidate.direction,
            prior_r=prior_r,
            ev_r=ev_r,
            confidence=confidence,
            sample_size=sample_size,
            total_score=candidate.total_score,
        )
        if ev_r < self.min_ev_r:
            ev.rejected = True
            ev.rejection = NoTradeReason.LOW_EV
        elif (
            sample_size >= self.stats.min_samples
            and confidence < self.min_ev_confidence
        ):
            ev.rejected = True
            ev.rejection = NoTradeReason.LOW_CONFIDENCE
        return ev

    def select(
        self,
        candidates: list[SignalCandidate],
        regime: str,
        regime_axes: str | None = None,
    ) -> MetaDecision:
        """Выбрать лучшего кандидата по EV; иначе NO_TRADE с причиной.

        ``regime_axes`` — композитный ключ Regime 2.0 (МТЗ §10): статистика
        сначала ищется по нему, фолбэк на legacy ``regime`` — внутри
        ``StrategyStatsStore``.

        Кандидаты, уже отвергнутые жёсткими гейтами пайплайна (rr,
        liquidity, correlation...), не рассматриваются.
        """
        evaluations: list[CandidateEvaluation] = []
        alive: list[tuple[SignalCandidate, CandidateEvaluation]] = []
        for candidate in candidates:
            if candidate.rejections:
                # Жёсткий гейт уже отклонил — фиксируем причину для лога.
                reason = REASON_MAP.get(
                    candidate.rejections[0], NoTradeReason.NO_VALID_SETUP
                )
                ev = CandidateEvaluation(
                    strategy=candidate.strategy,
                    direction=candidate.direction,
                    prior_r=candidate_prior_r(candidate),
                    ev_r=float("-inf"),
                    confidence=0.0,
                    sample_size=0,
                    total_score=candidate.total_score,
                    rejected=True,
                    rejection=reason,
                )
                evaluations.append(ev)
                continue
            ev = self.evaluate_candidate(candidate, regime, regime_axes=regime_axes)
            evaluations.append(ev)
            if not ev.rejected:
                alive.append((candidate, ev))

        if not alive:
            # Доминирующая причина: самая «частая» среди oтклонений;
            # LOW_EV важнее LOW_CONFIDENCE (порядок перечисления).
            reason = NoTradeReason.LOW_EV
            for priority in (
                NoTradeReason.LOW_EV,
                NoTradeReason.LOW_CONFIDENCE,
                NoTradeReason.LOW_LIQUIDITY,
                NoTradeReason.SPREAD_TOO_HIGH,
                NoTradeReason.CORRELATED_EXPOSURE,
                NoTradeReason.HALT,
                NoTradeReason.NO_VALID_SETUP,
            ):
                if any(e.rejection == priority for e in evaluations):
                    reason = priority
                    break
            return MetaDecision(chosen=None, reason_code=reason, evaluations=evaluations)

        alive.sort(key=lambda item: (item[1].ev_r, item[0].total_score), reverse=True)
        chosen, best_ev = alive[0]
        best_ev_r = best_ev.ev_r
        # Запоминаем EV выбранный на кандидате, чтобы движок мог его логировать.
        chosen.expected_edge_pct = round(best_ev_r * 100, 4)
        chosen.features["ev_r"] = best_ev_r
        chosen.features["ev_confidence"] = best_ev.confidence
        chosen.features["ev_sample_size"] = best_ev.sample_size
        return MetaDecision(chosen=chosen, reason_code=None, evaluations=evaluations)
