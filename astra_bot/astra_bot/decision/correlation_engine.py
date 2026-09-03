"""Correlation / BTC market filter."""

from __future__ import annotations

from dataclasses import dataclass

from .regime_engine import MarketRegime


@dataclass
class CorrelationReport:
    btc_regime: str
    eth_regime: str
    blocked: bool
    reason: str = ""

    def to_dict(self) -> dict:
        return self.__dict__


class CorrelationEngine:
    """Глобальный рыночный фильтр.

    Если BTC в PANIC/HIGH_VOLATILITY/STRONG_BEAR — большинство
    лонгов по альткоинам должно блокироваться на уровне риска.
    """

    RISK_OFF_REGIMES = {
        MarketRegime.PANIC.value,
        MarketRegime.HIGH_VOL.value,
        MarketRegime.STRONG_BEAR.value,
    }

    def assess(
        self,
        btc_regime: str = "UNKNOWN",
        eth_regime: str = "UNKNOWN",
        direction: str = "long",
        is_btc: bool = False,
    ) -> CorrelationReport:
        blocked = False
        reason = ""
        if (
            direction == "long"
            and not is_btc
            and btc_regime in self.RISK_OFF_REGIMES
        ):
            blocked = True
            reason = f"BTC в режиме {btc_regime} — альт-лонги заблокированы"
        return CorrelationReport(
            btc_regime=btc_regime,
            eth_regime=eth_regime,
            blocked=blocked,
            reason=reason,
        )
