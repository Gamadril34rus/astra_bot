"""Персистентный dedup HALT-алертов: раз в сутки на ключ (файл-based).

Контекст: Actions поднимает свежий процесс каждые 5 минут; до фикса
in-memory set терялся и HALT-алерт повторялся каждую сессию, пока лимит
пробит (жалоба владельца 09.09: «Daily loss limit reached: 1149.19 /
1082.97» приходило повторно). Фикс: models/halt_alerts.json {ключ: дата}.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from astra_bot.decision.halt_alerts import already_sent_today, load_sent, mark_sent
from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig


def _make_engine(tmp_path: Path, notifier: MagicMock):
    config = TradingEngineConfig(halt_alerts_path=str(tmp_path / "halt_alerts.json"))
    engine = TradingEngine(
        exchange=MagicMock(),
        pipeline=MagicMock(),
        notifier=notifier,
        config=config,
    )
    # Пробитый дневной лимит — фактические числа инцидента 09.09:
    # убыток −1149.19 при лимите 3% от 36098.87 (= 1082.97).
    engine.risk._daily_pnl = Decimal("-1149.19")
    engine.risk._initial_capital = Decimal("36098.87")
    engine.risk.config.daily_loss_limit = Decimal("0.03")
    return engine


def _cand():
    cand = MagicMock()
    cand.entry_price = Decimal("100")
    cand.stop_loss = Decimal("95")
    cand.take_profit = Decimal("110")
    cand.strategy = "test"
    return cand


def test_same_day_repeat_not_sent(tmp_path):
    """Повтор в тот же день НЕ шлёт — даже из «свежего процесса»."""
    notifier = MagicMock()
    engine = _make_engine(tmp_path, notifier)
    assert engine._risk_check_and_adjust("BTC-USDT", "long", _cand(), Decimal("1")) is None
    assert notifier.call_count == 1

    # «Свежий процесс»: новый движок, пустой in-memory set — но файл помнит.
    notifier2 = MagicMock()
    engine2 = _make_engine(tmp_path, notifier2)
    assert engine2._risk_check_and_adjust("BTC-USDT", "long", _cand(), Decimal("1")) is None
    assert notifier2.call_count == 0


def test_new_day_sends_again(tmp_path):
    """Новый день (UTC) — алерт доступен снова."""
    path = tmp_path / "halt_alerts.json"
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    path.write_text(f'{{"loss_limit_daily": "{yesterday}"}}\n', encoding="utf-8")

    notifier = MagicMock()
    engine = _make_engine(tmp_path, notifier)
    assert engine._risk_check_and_adjust("BTC-USDT", "long", _cand(), Decimal("1")) is None
    assert notifier.call_count == 1
    assert load_sent(path).get("loss_limit_daily") == datetime.now(UTC).date().isoformat()


def test_message_format_russian_and_single_emoji_source(tmp_path):
    """Формат: по-русски, цифры с пояснением, эмодзи НЕ в тексте."""
    notifier = MagicMock()
    engine = _make_engine(tmp_path, notifier)
    engine._risk_check_and_adjust("BTC-USDT", "long", _cand(), Decimal("1"))
    assert notifier.call_count == 1
    text = notifier.call_args[0][0]
    assert text.startswith("Остановка торговли (дневной лимит потерь).")
    assert "1149.19" in text
    assert "1082.97" in text
    assert "3%" in text
    assert "36098.87" in text
    assert "Открытые позиции: нет" in text
    # Эмодзи подставляет send_alert по severity — в тексте их нет.
    assert "⚠️" not in text
    assert "🚨" not in text
    # Английская причина Risk Engine владельцу не показывается.
    assert "Daily loss limit reached" not in text


def test_mark_not_written_when_send_fails(tmp_path):
    """Не удалось отправить — ключ НЕ помечаем: повторим в следующей сессии."""
    async def broken_notifier(text, severity="info"):
        raise RuntimeError("Telegram down")

    notifier = MagicMock(side_effect=broken_notifier)
    engine = _make_engine(tmp_path, notifier)
    path = tmp_path / "halt_alerts.json"
    assert not already_sent_today(path, "loss_limit_daily")
    engine._risk_check_and_adjust("BTC-USDT", "long", _cand(), Decimal("1"))
    assert not path.exists()


def test_async_send_awaits_then_marks(tmp_path):
    """Живой контур: есть event loop и notifier-корутина — ждём отправки,
    только после успеха пишем dedup-файл."""
    sent_texts: list[str] = []

    async def tg_notifier(text, severity="info"):
        sent_texts.append(text)

    notifier = MagicMock(side_effect=tg_notifier)
    engine = _make_engine(tmp_path, notifier)
    path = tmp_path / "halt_alerts.json"

    async def run():
        engine._risk_check_and_adjust("BTC-USDT", "long", _cand(), Decimal("1"))
        await asyncio.sleep(0.05)  # дать фоновой задаче завершиться

    asyncio.run(run())
    assert len(sent_texts) == 1
    assert already_sent_today(path, "loss_limit_daily")


def test_async_send_failure_not_marked(tmp_path):
    """Провал отправки в живом контуре — без отметки (повтор в след. сессии)."""

    async def broken_notifier(text, severity="info"):
        raise RuntimeError("Telegram down")

    notifier = MagicMock(side_effect=broken_notifier)
    engine = _make_engine(tmp_path, notifier)
    path = tmp_path / "halt_alerts.json"

    async def run():
        engine._risk_check_and_adjust("BTC-USDT", "long", _cand(), Decimal("1"))
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert not already_sent_today(path, "loss_limit_daily")


def test_mark_sent_cleanup_keeps_recent(tmp_path):
    """Отметка пишет {ключ: сегодня} и не падает на пустом/чужом файле."""
    path = tmp_path / "halt_alerts.json"
    old = (datetime.now(UTC) - timedelta(days=40)).date().isoformat()
    path.write_text(f'{{"state_EMERGENCY": "{old}"}}\n', encoding="utf-8")
    assert mark_sent(path, "loss_limit_daily")
    sent = load_sent(path)
    assert sent["loss_limit_daily"] == datetime.now(UTC).date().isoformat()
    # Старый (40 дней) ключ убран при записи.
    assert "state_EMERGENCY" not in sent


def test_load_sent_tolerates_broken_file(tmp_path):
    path = tmp_path / "halt_alerts.json"
    path.write_text("{битый json", encoding="utf-8")
    assert load_sent(path) == {}
    assert not already_sent_today(path, "loss_limit_daily")
