"""Single RiskConfig source of truth (TZ P0.2)."""

from __future__ import annotations

from decimal import Decimal

from astra_bot.core.config import RiskConfig as CoreRiskConfig
from astra_bot.engines.risk_engine import RiskConfig as EngineRiskConfig
from astra_bot.engines.risk_engine import RiskEngine


def test_engine_reexports_core_class():
    assert CoreRiskConfig is EngineRiskConfig


def test_defaults_unchanged():
    cfg = CoreRiskConfig()
    assert cfg.risk_per_trade == Decimal("0.004")
    assert cfg.daily_loss_limit == Decimal("0.02")
    assert cfg.max_open_positions == 5
    assert cfg.default_beta == Decimal("1.5")
    assert cfg.max_gross_exposure_pct is None


def test_env_overrides_yaml(monkeypatch):
    monkeypatch.setenv("ASTRA_RISK_PER_TRADE", "0.01")
    monkeypatch.setenv("ASTRA_MAX_OPEN_POSITIONS", "3")
    cfg = CoreRiskConfig.from_dict({"risk_per_trade": 0.004, "max_open_positions": 5})
    assert cfg.risk_per_trade == Decimal("0.01")
    assert cfg.max_open_positions == 3


def test_paper_runtime_keeps_trading_engine_numbers():
    cfg = CoreRiskConfig.paper_runtime(
        risk_per_trade=Decimal("0.01"),
        max_open_positions=3,
        max_exposure_pct=Decimal("0.30"),
    )
    assert cfg.daily_loss_limit == Decimal("0.03")
    assert cfg.weekly_loss_limit == Decimal("0.06")
    engine = RiskEngine(cfg)
    assert engine.config is cfg


def test_yaml_portfolio_fields():
    cfg = CoreRiskConfig.from_dict(
        {
            "max_gross_exposure_pct": 0.25,
            "correlation_groups": {"BTC-USDT": "btc"},
            "default_beta": 1.2,
        }
    )
    assert cfg.max_gross_exposure_pct == Decimal("0.25")
    assert cfg.correlation_groups["BTC-USDT"] == "btc"
    assert cfg.default_beta == Decimal("1.2")
