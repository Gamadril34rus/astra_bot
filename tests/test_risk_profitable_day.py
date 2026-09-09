"""Блок D: дневной/недельный лимит считается от УБЫТКА, не от abs(PnL)."""

from __future__ import annotations

from decimal import Decimal

from astra_bot.engines.risk_engine import RiskConfig, RiskEngine


def _engine() -> RiskEngine:
    eng = RiskEngine(
        RiskConfig(
            risk_per_trade=Decimal("0.01"),
            daily_loss_limit=Decimal("0.03"),
            weekly_loss_limit=Decimal("0.06"),
        )
    )
    eng.set_capital(Decimal("10000"), Decimal("10000"))
    return eng


def _check(eng: RiskEngine):
    return eng.check_trade(
        "ETH-USDT", "long",
        Decimal("100"), Decimal("99"), Decimal("102"), Decimal("1"),
    )


def test_profitable_day_not_blocked():
    eng = _engine()
    eng.record_trade("BTC-USDT", "long", Decimal("100"), Decimal("1"), Decimal("500"), True)
    assert eng.daily_loss_pct == 0
    res = _check(eng)
    assert res.approved, res.reason


def test_losing_day_blocked():
    eng = _engine()
    eng.record_trade("BTC-USDT", "long", Decimal("100"), Decimal("1"), Decimal("-500"), False)
    assert eng.daily_loss_pct == Decimal("5")
    res = _check(eng)
    assert not res.approved
    assert "Daily loss" in (res.reason or "")
