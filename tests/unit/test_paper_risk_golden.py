"""Golden paper-risk numbers (TZ P0.2 / review A3)."""

from __future__ import annotations

import inspect
from decimal import Decimal
from pathlib import Path

from astra_bot.core.config import DEFAULT_DRAWDOWN_ADAPTATION, RiskConfig, SystemConfig
from astra_bot.core.instruments import TRADING_UNIVERSE
from astra_bot.decision.config import DecisionConfig
from astra_bot.decision.trading_engine import TradingEngineConfig
from astra_bot.engines.position_sizer import calculate_position_size
from astra_bot.engines.risk_engine import RiskConfig as EngineRiskConfig

EXPECTED_INSTRUMENTS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "ADA/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "DOT/USDT",
    "TRX/USDT",
]


def test_single_risk_config_class():
    assert EngineRiskConfig is RiskConfig
    assert RiskConfig.__module__ == "astra_bot.core.config"


def test_risk_config_dataclass_defaults():
    cfg = RiskConfig()
    assert cfg.risk_per_trade == Decimal("0.004")
    assert cfg.daily_loss_limit == Decimal("0.02")
    assert cfg.weekly_loss_limit == Decimal("0.04")
    assert cfg.max_open_positions == 5


def test_trading_engine_paper_literals():
    te = TradingEngineConfig()
    assert te.risk_per_trade_pct == Decimal("0.01")
    assert te.max_notional_pct == Decimal("0.10")
    assert te.max_open_positions == 3
    assert te.max_same_direction == 2
    assert te.leverage_max == 100
    src = Path("astra_bot/decision/trading_engine.py").read_text(encoding="utf-8")
    # Owner 24.09: min_rr=2.0 aligned with clamp_take_rr [2.0, 2.3].
    # 0.7 was a bug; 3.0 was uncoordinated with the take clamp.
    assert "cfg.min_rr = 2.0" in src
    assert DecisionConfig().min_rr == 2.0


def test_position_sizer_d2_cap():
    sig = inspect.signature(calculate_position_size)
    assert sig.parameters["risk_per_trade_pct"].default == Decimal("0.01")
    assert sig.parameters["max_notional_pct"].default == Decimal("0.10")
    assert sig.parameters["max_risk_pct"].default == Decimal("0.03")


def test_drawdown_adaptation_ladder():
    assert [
        {"drawdown": Decimal("0"), "risk_multiplier": Decimal("1.0")},
        {"drawdown": Decimal("0.03"), "risk_multiplier": Decimal("0.75")},
        {"drawdown": Decimal("0.05"), "risk_multiplier": Decimal("0.5")},
        {"drawdown": Decimal("0.08"), "risk_multiplier": Decimal("0.0")},
    ] == DEFAULT_DRAWDOWN_ADAPTATION


def test_readme_universe_guard_follows_trading_universe():
    instruments = SystemConfig().instruments
    assert instruments == EXPECTED_INSTRUMENTS
    readme = Path("README.md").read_text(encoding="utf-8")
    tickers = [symbol.split("/")[0] for symbol in TRADING_UNIVERSE]
    assert len(set(tickers)) == len(tickers), "дубли тикеров в TRADING_UNIVERSE"
    missing = [ticker for ticker in tickers if ticker not in readme]
    assert not missing, f"README не упоминает тикеры живого юниверса: {missing}"


def test_backup_restart_is_no_not_daily():
    text = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert 'restart: "no"' in text
    assert 'profiles: ["backup"]' in text
    assert "\n    restart: daily" not in text
