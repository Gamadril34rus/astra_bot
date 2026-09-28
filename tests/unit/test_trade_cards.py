"""TG-карточки сделок (этап 4): текст, дедуп СТОП, выключатель."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig


def _engine(enabled=True):
    eng = object.__new__(TradingEngine)
    eng.config = TradingEngineConfig()
    eng._trade_cards_enabled = enabled
    eng._trade_card_keys = set()
    sent: list[str] = []
    eng._notify = sent.append  # перекрываем метод инстанс-атрибутом
    return eng, sent


def test_entry_card_has_symbol_money_and_dir(monkeypatch):
    eng, _ = _engine()
    monkeypatch.setenv("ENVIRONMENT", "paper")
    pos = SimpleNamespace(
        id="t1", symbol="BTC-USDT", direction="long",
        entry_price=Decimal("100"), stop_loss=Decimal("95"),
        take_profits=[Decimal("110")], strategy="mai_cross",
    )
    text = eng._card_entry_text(pos)
    assert "BTC-USDT" in text and "ЛОНГ" in text
    assert "$100" in text and "$95" in text and "$110" in text
    assert "(paper)" in text


def test_exit_card_ru_reason_and_r():
    eng, _ = _engine()
    d = {"id": "t1", "symbol": "BTC-USDT", "direction": "short",
         "exit_price": "98.5", "r_multiple": 1.2, "exit_reason": "stop_loss",
         "strategy": "mai_cross"}
    text = eng._card_exit_text(d)
    assert "ШОРТ" in text and "+1.20R" in text and "сработал стоп" in text
    assert "$98.5" in text


def test_stop_card_dedup_same_symbol_price():
    eng, sent = _engine(enabled=True)
    d = {"id": "t1", "symbol": "BTC-USDT", "direction": "long",
         "exit_price": "95", "r_multiple": -1.0, "exit_reason": "stop_loss",
         "strategy": "x"}
    key = f"stop:{d['symbol']}:{d['exit_price']}"
    eng._send_trade_card(eng._card_exit_text(d), key)
    eng._send_trade_card(eng._card_exit_text(d), key)
    assert len(sent) == 1  # повторный одинаковый СТОП не уходит


def test_cards_disabled_by_default():
    eng, sent = _engine(enabled=False)
    eng._send_trade_card("тест", "k1")
    assert sent == []
    assert eng._trade_card_keys == set()
