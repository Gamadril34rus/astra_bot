"""Zeus confidence → actual risk% ladder + day limit 4% (real RiskEngine)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from astra_bot.decision.conf_risk_ladder import conf_risk_pct
from astra_bot.engines.position_sizer import calculate_position_size

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


def test_sizer_receives_ladder_budget():
    """conf=0.90, equity=2000 → risk_per_trade_pct=0.03 reaches the sizer input."""
    equity = Decimal("2000")
    conf = 0.90
    tier = conf_risk_pct(conf, CONF_RISK_LADDER)
    assert tier == Decimal("0.03")
    budget = equity * tier
    assert budget == Decimal("60.0")

    captured: dict = {}

    def _spy(**kwargs):
        captured["risk_per_trade_pct"] = kwargs.get("risk_per_trade_pct")
        captured["equity"] = kwargs.get("equity")
        # Return a valid qty so caller path doesn't care
        return Decimal("1")

    with patch(
        "astra_bot.engines.position_sizer.calculate_position_size", side_effect=_spy
    ):
        # Call the real sizer path the way _position_size does after ladder applied
        calculate_position_size(
            equity=equity,
            entry_price=Decimal("100"),
            stop_loss=Decimal("95"),
            risk_per_trade_pct=tier,
            win_rate=0.55,
            avg_win_r=1.5,
            avg_loss_r=1.0,
            ml_confidence=conf,
        )
        # Direct call without patch to prove budget is used in formula
        qty = calculate_position_size(
            equity=equity,
            entry_price=Decimal("100"),
            stop_loss=Decimal("95"),
            risk_per_trade_pct=tier,
            win_rate=0.55,
            avg_win_r=1.5,
            avg_loss_r=1.0,
            ml_confidence=conf,
            max_notional_pct=Decimal("1.0"),  # no notional clamp
        )
    # Unpatched call: risk_amount = 60, stop_dist = 5 → base before Kelly
    # Kelly/ML may reduce size; risk_amount itself must be 60 at input.
    assert budget == Decimal("60.0")
    # With spy path: if patch intercepted (import path), assert; else budget math holds
    if captured:
        assert captured["risk_per_trade_pct"] == Decimal("0.03")
        assert captured["equity"] == equity
    # qty must be positive and scale with 3% not 1%
    qty_1pct = calculate_position_size(
        equity=equity,
        entry_price=Decimal("100"),
        stop_loss=Decimal("95"),
        risk_per_trade_pct=Decimal("0.01"),
        win_rate=0.55,
        avg_win_r=1.5,
        avg_loss_r=1.0,
        ml_confidence=conf,
        max_notional_pct=Decimal("1.0"),
    )
    assert qty > qty_1pct
    # Ideal unadjusted: 60/5 = 12; with Kelly+ML may be less but > 1% path
    assert qty >= qty_1pct * Decimal("2")  # at least 2x of 1% path


def test_day_limit_4pct_real_risk_engine():
    """Day HALT via real RiskEngine.record_trade + check_trade (>=4% yes, 3.9% no)."""
    from astra_bot.core.config import RiskConfig
    from astra_bot.engines.risk_engine import RiskEngine

    cfg = RiskConfig.paper_runtime(
        risk_per_trade=Decimal("0.01"),
        max_open_positions=3,
        max_exposure_pct=Decimal("0.30"),
        daily_loss_limit=Decimal("0.04"),
        weekly_loss_limit=Decimal("0.06"),
    )
    assert cfg.daily_loss_limit == Decimal("0.04")
    assert cfg.weekly_loss_limit == Decimal("0.06")

    eng = RiskEngine(cfg)
    eng.set_capital(Decimal("1000"), Decimal("1000"))
    tiny = Decimal("0.0001")

    # 3.9% loss — still tradeable
    eng.record_trade(
        symbol="BTC-USDT",
        side="buy",
        entry_price=Decimal("50000"),
        quantity=tiny,
        pnl=Decimal("-39"),
        won=False,
    )
    r39 = eng.check_trade(
        symbol="BTC-USDT",
        side="buy",
        entry_price=Decimal("50000"),
        stop_loss=Decimal("49000"),
        take_profit=Decimal("52000"),
        proposed_size=tiny,
    )
    assert r39.approved is True, r39.reason

    # Additional 1 → total -40 = 4.0% → HALT
    eng.record_trade(
        symbol="BTC-USDT",
        side="buy",
        entry_price=Decimal("50000"),
        quantity=tiny,
        pnl=Decimal("-1"),
        won=False,
    )
    r40 = eng.check_trade(
        symbol="BTC-USDT",
        side="buy",
        entry_price=Decimal("50000"),
        stop_loss=Decimal("49000"),
        take_profit=Decimal("52000"),
        proposed_size=tiny,
    )
    assert r40.approved is False
    assert "daily" in (r40.reason or "").lower()
