"""Zeus confidence → risk% ladder + day limit 4%."""

from __future__ import annotations

from decimal import Decimal

from astra_bot.core.config import RiskConfig
from astra_bot.decision.conf_risk_ladder import conf_risk_pct

CONF_RISK_LADDER = ((0.88, 3.0), (0.80, 2.0), (0.70, 1.5))


def test_conf_risk_ladder_mapping():
    base = Decimal("0.01")
    cases = [
        (0.69, Decimal("0.01")),
        (0.70, Decimal("0.015")),
        (0.79, Decimal("0.015")),
        (0.80, Decimal("0.02")),
        (0.87, Decimal("0.02")),
        (0.88, Decimal("0.03")),
        (None, Decimal("0.01")),
    ]
    for conf, want in cases:
        got = conf_risk_pct(conf, CONF_RISK_LADDER, base)
        assert got == want, (conf, got, want)


def test_risk_budget_uses_ladder():
    equity = Decimal("2000")
    assert equity * conf_risk_pct(0.90, CONF_RISK_LADDER) == Decimal("60.0")
    assert equity * conf_risk_pct(0.75, CONF_RISK_LADDER) == Decimal("30.0")
    assert equity * conf_risk_pct(None, CONF_RISK_LADDER) == Decimal("20.0")


def test_day_limit_4pct():
    """Day HALT at >=4% of capital (mirrors RiskEngine daily_loss check)."""
    cfg = RiskConfig.paper_runtime(
        risk_per_trade=Decimal("0.01"),
        max_open_positions=3,
        max_exposure_pct=Decimal("0.30"),
        daily_loss_limit=Decimal("0.04"),
        weekly_loss_limit=Decimal("0.06"),
    )
    assert cfg.daily_loss_limit == Decimal("0.04")
    assert cfg.weekly_loss_limit == Decimal("0.06")
    initial = Decimal("1000")
    max_daily = initial * cfg.daily_loss_limit

    def would_halt(daily_pnl: Decimal) -> bool:
        daily_loss = max(Decimal("0"), -daily_pnl)
        return daily_loss >= max_daily

    assert would_halt(Decimal("-39")) is False  # 3.9%
    assert would_halt(Decimal("-40")) is True  # 4.0%
    assert would_halt(Decimal("-41")) is True
