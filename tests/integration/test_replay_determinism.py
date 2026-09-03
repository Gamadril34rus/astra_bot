"""Replay-детерминизм (Этап 8): одинаковый набор данных → одинаковый
набор сделок. Это требование к «переигрыванию» CI-сессий и бэктестов:
результат не должен зависеть от случайных id/времени wall-clock.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tests.integration.test_main_tick import make_bot
from tests.integration.test_meta_strategy_execution import (
    FeedStub,
    gen_candles,
    stop_hit_bar,
)


def _norm_trades(path: Path) -> list[dict]:
    """Сделки без wall-clock/уникальных id — только торговая семантика."""
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        d.pop("id", None)
        d.pop("opened_at", None)
        d.pop("closed_at", None)
        out.append(d)
    return out


def _scenario(tmp_path: Path, monkeypatch) -> list[dict]:
    """Одинаковый сценарий: тик (вход) → стоп-бар (выход)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    # Изоляция: тестовые сделки не засоряют реальные lessons.
    monkeypatch.setattr(
        "astra_bot.decision.trading_engine.append_lessons", lambda trades: 0
    )
    bot = make_bot(tmp_path, FeedStub(gen_candles(230)), monkeypatch)
    eng = bot._trading_engine
    asyncio.run(bot._tick())
    assert len(eng.broker.positions) == 1, "сценарий должен открыть позицию"
    pos = eng.broker.positions[0]
    eng.exchange.candles = [*eng.exchange.candles, stop_hit_bar(eng.exchange.candles[-1], pos.stop_loss)]
    bot._last_tick_at = 0.0
    asyncio.run(bot._tick())
    return _norm_trades(eng.broker.trades_path)


def test_replay_produces_identical_trades(tmp_path, monkeypatch):
    """Два независимых прогона (чистые state-каталоги) → идентичные
    последовательности сделок (после нормализации id/времени)."""
    seq1 = _scenario(tmp_path / "run1", monkeypatch)
    seq2 = _scenario(tmp_path / "run2", monkeypatch)
    assert seq1, "сценарий должен завершить хотя бы одну сделку"
    assert seq1 == seq2, (
        "Replay не детерминирован:\n"
        f"run1={json.dumps(seq1, ensure_ascii=False)}\n"
        f"run2={json.dumps(seq2, ensure_ascii=False)}"
    )


def test_replay_state_bundle_equivalent(tmp_path, monkeypatch):
    """State-бандлы двух прогонов сходятся по торговому содержимому."""
    (tmp_path / "r1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "r2").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "astra_bot.decision.trading_engine.append_lessons", lambda trades: 0
    )
    bot1 = make_bot(tmp_path / "r1", FeedStub(gen_candles(230)), monkeypatch)
    asyncio.run(bot1._tick())
    b1 = json.loads(bot1._trading_engine.state_store.path.read_text())

    bot2 = make_bot(tmp_path / "r2", FeedStub(gen_candles(230)), monkeypatch)
    asyncio.run(bot2._tick())
    b2 = json.loads(bot2._trading_engine.state_store.path.read_text())

    for key in ("realized_pnl", "initial_capital"):
        assert b1["paper"]["broker_state"][key] == b2["paper"]["broker_state"][key]
    p1 = b1["paper"]["broker_state"]["positions"][0]
    p2 = b2["paper"]["broker_state"]["positions"][0]
    for key in ("symbol", "direction", "entry_price", "stop_loss", "quantity"):
        assert p1[key] == p2[key], key
