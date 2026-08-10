"""Тесты загрузчика конфигурации."""

import pytest
from astra_bot.core.config import SystemConfig, _expand_env


def test_expand_env_replaces_simple_variable(monkeypatch):
    monkeypatch.setenv("ASTRA_TEST_TOKEN", "secret")
    assert _expand_env("token: ${ASTRA_TEST_TOKEN}") == "token: secret"


def test_expand_env_uses_default_after_colon_dash(monkeypatch):
    monkeypatch.delenv("ASTRA_MISSING", raising=False)
    assert _expand_env("${ASTRA_MISSING:-fallback}") == "fallback"


def test_expand_env_walks_nested_structures():
    data = {"a": ["${NOPE:-x}", {"b": "${NOPE:-y}"}], "c": 1}
    assert _expand_env(data) == {"a": ["x", {"b": "y"}], "c": 1}


def test_flat_trading_section_is_supported():
    cfg = SystemConfig.from_dict(
        {
            "trading": {
                "instruments": ["BTC/USDT", "ETH/USDT"],
                "strategies": {
                    "momentum": {"enabled": False, "weight": 1.0},
                },
            }
        }
    )
    assert cfg.instruments == ["BTC/USDT", "ETH/USDT"]
    assert cfg.strategies["momentum"]["enabled"] is False


def test_telegram_unset_placeholders_become_empty(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg = SystemConfig.from_dict(
        {
            "telegram": {
                "bot_token": "${TELEGRAM_BOT_TOKEN}",
                "allowed_user_ids": ["${TELEGRAM_USER_ID}"],
                "admin_user_ids": ["${TELEGRAM_ADMIN_ID}"],
            }
        }
    )
    assert cfg.telegram.bot_token == ""
    assert cfg.telegram.allowed_user_ids == []
    assert cfg.telegram.admin_user_ids == []


def test_risk_config_from_dict_accepts_decimal_inputs():
    cfg = SystemConfig.from_dict({"risk": {"risk_per_trade": 0.01}})
    assert cfg.risk.risk_per_trade.is_nan() is False
    assert float(cfg.risk.risk_per_trade) == pytest.approx(0.01)
