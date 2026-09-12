"""Бэклог B1: жизненный цикл HALT (семантика закреплена тестами).

Владелец (13.09.2026): поведение HALT НЕ менять, но закрепить семантику:
какие остановки временные, какие переживают рестарт, кто их снимает.

Факты кода (`astra_bot/engines/risk_engine.py`):

  - дневной/недельный лимит потерь — только отказ входа, `trading_enabled`
    остаётся True (строки 437-464);
  - просадка ≥ hard(8%)/≥ emergency(10%) — STOP/EMERGENCY,
    `trading_enabled = False` (строки 798-812);
  - состояние пересобирается из `models/paper_trades.jsonl` на старте
    процесса (строки 151-216) → HALT переживает рестарт, пока условия живы;
  - осознанный paper-автосброс: ≥ 24ч без сделок и нулевые окна → HWM
    сбрасывается к текущему капиталу (строки 218-249) — «в реальном
    контуре такой автосброс не делается».
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from astra_bot.engines.risk_engine import RiskEngine


def _trade(pnl: float, hours_ago: float) -> dict:
    return {
        "symbol": "BTC-USDT",
        "direction": "long",
        "entry_price": 100.0,
        "quantity": 1.0,
        "pnl": pnl,
        "closed_at": int(
            (datetime.now(UTC) - timedelta(hours=hours_ago)).timestamp() * 1000
        ),
    }


def _check(engine: RiskEngine):
    return engine.check_trade(
        symbol="BTC-USDT",
        side="long",
        entry_price=Decimal("100"),
        stop_loss=Decimal("99"),
        take_profit=Decimal("103"),
        proposed_size=Decimal("1"),
    )


def test_halt_survives_restart_while_drawdown_alive():
    """HALT по просадке: reatore из журнала → HALT снова (состояние живёт)."""
    trades = [_trade(-90, 1)]  # 9% просадки ≥ hard 8%

    first = RiskEngine()
    first.restore_from_trades(trades, Decimal("1000"))
    assert first.trading_enabled is False
    assert first.risk_state.value == "STOP"
    assert _check(first).approved is False

    # Рестарт процесса: CI поднимает новый процесс каждые 5 минут —
    # state-файла у риска нет, состояние восстанавливается из сделок.
    restarted = RiskEngine()
    restarted.restore_from_trades(trades, Decimal("1000"))
    assert restarted.trading_enabled is False
    assert restarted.risk_state.value == "STOP"
    verdict = _check(restarted)
    assert verdict.approved is False
    assert "disabled" in (verdict.reason or "").lower()


def test_emergency_state_is_live_on_fresh_drawdown():
    """EMERGENCY (≥10%) при свежих сделках: торговля выключена."""
    engine = RiskEngine()
    engine.restore_from_trades([_trade(-120, 1)], Decimal("1000"))
    assert engine.current_drawdown >= 10.0
    assert engine.risk_state.value == "EMERGENCY"
    assert engine.trading_enabled is False


def test_emergency_auto_resets_after_24h_without_trades():
    """Осознанный paper-автосброс: ≥24ч без сделок и нулевые окна → HWM=equity."""
    trades = [_trade(-120, 8 * 24)]  # 12% просадки, но окна 24ч/7д пусты
    engine = RiskEngine()
    assert (Decimal("1000") - Decimal("880")) / Decimal("1000") * 100 >= Decimal("10")
    engine.restore_from_trades(trades, Decimal("1000"))

    assert engine.risk_state.value == "NORMAL"
    assert engine.trading_enabled is True
    assert engine.daily_pnl == Decimal("0")
    assert engine.weekly_pnl == Decimal("0")
    # HWM сброшен к текущему капиталу — просадка обнулилась.
    assert engine._high_water_mark == engine._current_equity == Decimal("880")
    assert engine.current_drawdown == Decimal("0")


def test_daily_loss_halt_is_temporary_and_does_not_disable_trading():
    """Дневной лимит: только отказ входов; снимается сам по выходу из окна 24ч."""
    engine = RiskEngine()
    engine.restore_from_trades([_trade(-25, 2)], Decimal("1000"))  # 2.5% ≥ 2%
    assert engine.trading_enabled is True
    verdict = _check(engine)
    assert verdict.approved is False
    assert "Daily loss limit" in (verdict.reason or "")

    # Тот же минус, но из окна 24ч он уже вышел → входы снова разрешены.
    later = RiskEngine()
    later.restore_from_trades([_trade(-25, 30)], Decimal("1000"))
    assert later.daily_pnl == Decimal("0")
    assert later.trading_enabled is True
    assert _check(later).approved is True
