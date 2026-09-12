"""Анти-дребезг: кулдаун 20 мин после выхода по стопу (решение владельца 12.09).

Контракт: та же стратегия + тот же символ + сторона не входят 20 минут
после выхода по стопу. Реестр живёт в существующем state брокера
(paper_positions.json), т.к. процесс пересоздаётся каждые ~5 минут и
память не выживает. Только stop_loss; VOL_EXPANSION и прочие forced — нет.
"""

from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import MagicMock

from astra_bot.decision.broker import PaperBroker
from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig


def _broker(tmp_path, **kw):
    return PaperBroker(
        state_path=tmp_path / "paper_positions.json",
        trades_path=tmp_path / "paper_trades.jsonl",
        initial_capital=Decimal("10000"),
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
        **kw,
    )


def _engine(tmp_path, **cfg):
    return TradingEngine(
        exchange=MagicMock(),
        broker=_broker(tmp_path),
        config=TradingEngineConfig(**cfg),
    )


class _T:
    """Минимальная закрытая сделка (атрибуты как у ClosedTrade)."""

    def __init__(self, **kw):
        base = dict(
            id="pos-1",
            symbol="LINK-USDT",
            direction="short",
            entry_price=100.0,
            exit_price=100.5,
            quantity=Decimal("1"),
            pnl=-5.0,
            pnl_pct=-0.5,
            exit_reason="stop_loss",
            strategy="descending_triangle",
            opened_at=int(time.time() * 1000) - 600_000,
            closed_at=int(time.time() * 1000),
            fees=0.0,
            funding=0.0,
            r_multiple=-0.5,
            mfe_r=0.1,
            mae_r=-0.6,
            regime="RANGE",
            timeframe="1h",
            regime_axes="",
        )
        base.update(kw)
        self.__dict__.update(base)


# --------------------------------------------------------------- регистрация
def test_stop_loss_registers_cooldown(tmp_path):
    eng = _engine(tmp_path)
    eng._record_closed([_T()])
    key = eng._cooldown_key("descending_triangle", "LINK-USDT", "short")
    assert eng.broker.cooldown_remaining_ms(key) > 0
    # окно — ровно 20 минут по умолчанию
    assert eng._cooldown_ttl_ms() == 20 * 60_000
    assert eng.broker.cooldowns[key] > int(time.time() * 1000)


def test_forced_exits_do_not_register(tmp_path):
    eng = _engine(tmp_path)
    for reason in ("VOL_EXPANSION", "mae_cut", "regime_exit", "tp1"):
        t = _T(id=f"pos-{reason}", exit_reason=reason)
        eng._record_closed([t])
    # ни один forced/тейк не ставит кулдаун
    assert eng.broker.cooldowns == {}


# ------------------------------------------------------------------- гейт
def test_gate_blocks_within_window(tmp_path):
    eng = _engine(tmp_path)
    eng._record_closed([_T()])
    assert eng._cooldown_blocks("descending_triangle", "LINK-USDT", "short") is True


def test_gate_passes_after_expiry(tmp_path):
    eng = _engine(tmp_path)
    key = eng._cooldown_key("descending_triangle", "LINK-USDT", "short")
    eng.broker.register_cooldown(key, int(time.time() * 1000) + 50)
    assert eng._cooldown_blocks("descending_triangle", "LINK-USDT", "short") is True
    time.sleep(0.06)
    assert eng._cooldown_blocks("descending_triangle", "LINK-USDT", "short") is False


def test_gate_passes_other_side_and_symbol(tmp_path):
    eng = _engine(tmp_path)
    eng._record_closed([_T()])
    # та же пара, другая сторона — проход
    assert eng._cooldown_blocks("descending_triangle", "LINK-USDT", "long") is False
    # тот же символ+сторона, другая стратегия — проход
    assert eng._cooldown_blocks("flag", "LINK-USDT", "short") is False
    # другая пара — проход
    assert eng._cooldown_blocks("descending_triangle", "UNI-USDT", "short") is False


# ------------------------------ главный тест: переживает пересоздание движка
def test_cooldown_survives_engine_recreation_from_state(tmp_path):
    eng1 = _engine(tmp_path)
    eng1._record_closed([_T()])
    # реестр обязан попасть в существующий state-файл брокера
    assert (tmp_path / "paper_positions.json").exists()

    # новый движок (новый процесс) читает тот же state
    eng2 = _engine(tmp_path)
    key = eng2._cooldown_key("descending_triangle", "LINK-USDT", "short")
    assert eng2.broker.cooldown_remaining_ms(key) > 0
    assert eng2._cooldown_blocks("descending_triangle", "LINK-USDT", "short") is True


# ------------------------------------------------------------- рост ограничен
def test_expired_cooldowns_purged_on_save(tmp_path):
    broker = _broker(tmp_path)
    broker.register_cooldown("a|X-USDT|long", int(time.time() * 1000) - 1000)
    broker.register_cooldown("b|Y-USDT|short", int(time.time() * 1000) + 600_000)
    broker.save()
    # просроченная запись не переживает сохранение
    assert "a|X-USDT|long" not in broker.cooldowns
    assert "b|Y-USDT|short" in broker.cooldowns
    fresh = _broker(tmp_path)
    assert "a|X-USDT|long" not in fresh.cooldowns
    assert "b|Y-USDT|short" in fresh.cooldowns
