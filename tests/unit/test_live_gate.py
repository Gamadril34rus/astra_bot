"""Двойной safety-gate live-ордеров (Этап 9): fail-closed по умолчанию."""

from __future__ import annotations

from astra_bot.core.config import load_settings
from astra_bot.main import live_orders_allowed


def _load(tmp_path, *, trading_enabled: bool = False):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        "system:\n"
        "  paper_trading: true\n"
        f"  trading_enabled: {str(trading_enabled).lower()}\n"
        "trading:\n"
        "  instruments:\n"
        "    - BTC/USDT\n",
        encoding="utf-8",
    )
    load_settings(str(cfg))


class TestLiveGate:
    def test_disabled_by_default(self, tmp_path, monkeypatch):
        """Никакого env → live ЗАПРЕЩЁН (fail-closed)."""
        monkeypatch.delenv("ENABLE_LIVE_ORDERS", raising=False)
        _load(tmp_path)
        allowed, reasons = live_orders_allowed()
        assert allowed is False
        assert any("ENABLE_LIVE_ORDERS" in r for r in reasons)
        assert any("readiness" in r for r in reasons)

    def test_env_true_but_config_off(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENABLE_LIVE_ORDERS", "true")
        _load(tmp_path, trading_enabled=False)
        allowed, reasons = live_orders_allowed()
        assert allowed is False
        assert any("trading_enabled" in r for r in reasons)

    def test_env_and_config_but_not_ready(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENABLE_LIVE_ORDERS", "true")
        _load(tmp_path, trading_enabled=True)
        # readiness.evaluate() на чистом state: данных нет → не готов.
        allowed, reasons = live_orders_allowed()
        assert allowed is False
        assert any("readiness" in r for r in reasons)

    def test_all_three_conditions_pass(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENABLE_LIVE_ORDERS", "true")
        _load(tmp_path, trading_enabled=True)
        ready = {
            "ready": True, "score": 90, "threshold": 90,
            "trading_days": 40, "total_trades": 300, "win_rate": 0.6,
            "profit_factor": 1.5, "max_drawdown_pct": 3.0, "sharpe": 1.4,
            "total_pnl": 100.0, "checks": [],
        }
        import astra_bot.main as m

        monkeypatch.setattr(m.readiness, "evaluate", lambda: ready)
        allowed, reasons = live_orders_allowed()
        assert allowed is True
        assert reasons == []

    def test_env_case_insensitive(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENABLE_LIVE_ORDERS", "TRUE")
        _load(tmp_path)
        # env-часть гейта пройдена, остальные всё ещё закрыты.
        allowed, reasons = live_orders_allowed()
        assert allowed is False
        assert not any("ENABLE_LIVE_ORDERS" in r for r in reasons)

    def test_env_typo_value_rejected(self, tmp_path, monkeypatch):
        for bad in ("1", "yes", "on", ""):
            monkeypatch.setenv("ENABLE_LIVE_ORDERS", bad)
            _load(tmp_path)
            allowed, reasons = live_orders_allowed()
            assert allowed is False, bad
            assert any("ENABLE_LIVE_ORDERS" in r for r in reasons), bad
