"""Интеграция гейтов входа в живой тик (PR #78, часть 3).

Тот же harness, что и у test_main_tick: реальный путь
AstraBot._tick → TradingEngine.process_symbol → risk → PaperBroker. Проверяем
границу ответственности: тень считает и пишет наблюдение, но вход НЕ трогает;
живой режим — снимает вход, не трогая остальной контур.
"""

from __future__ import annotations

import asyncio
import json

from astra_bot.decision.trading_engine import TradingEngine
from astra_bot.ml.no_trade_observations import NoTradeObservationLog
from tests.integration.test_main_tick import FeedStub, make_bot
from tests.integration.test_meta_strategy_execution import gen_candles


def _engine(tmp_path, monkeypatch, **gate_cfg) -> TradingEngine:
    bot = make_bot(tmp_path, FeedStub(gen_candles()), monkeypatch)
    assert bot._trading_engine is not None
    eng = bot._trading_engine
    for key, value in gate_cfg.items():
        setattr(eng.config, key, value)
    eng.entry_gates = type(eng.entry_gates).from_engine_config(eng.config)
    # Журнал наблюдений — в tmp, тест не имеет права трогать models/
    eng.obs_log = NoTradeObservationLog(
        observations_path=tmp_path / "obs.jsonl",
        outcomes_path=tmp_path / "outcomes.json",
    )
    return eng


def test_all_gates_off_changes_nothing(tmp_path, monkeypatch) -> None:
    eng = _engine(tmp_path, monkeypatch)  # дефолт: живой выключен, тень включена
    assert eng.config.entry_gates_enabled is False
    asyncio.run(eng.step())
    assert len(eng.broker.positions) == 1
    # тень ничего не блокирует: счётчик может считать, вход — нет
    assert all(not k.endswith(":live") for k in eng.entry_gate_stats)


def test_shadow_counts_and_records_without_blocking(tmp_path, monkeypatch) -> None:
    eng = _engine(
        tmp_path, monkeypatch,
        entry_gates_enabled=False, entry_gates_shadow_enabled=True,
        entry_gate_max_costs_r=0.0,  # любое положительное costs_r = «дорогой вход»
    )
    asyncio.run(eng.step())
    assert len(eng.broker.positions) == 1, "тень не имеет права снимать вход"
    assert eng.entry_gate_stats, "тень обязана считать срабатывания"
    assert all(k.endswith(":shadow") for k in eng.entry_gate_stats)
    rows = [
        json.loads(line)
        for line in (tmp_path / "obs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    gate_rows = [r for r in rows if r.get("rejection_stage") == "entry_gate"]
    assert gate_rows, "срабатывание тени обязано оставить NO_TRADE-наблюдение"
    assert gate_rows[0]["reason_code"].startswith("ENTRY_GATE_")
    assert gate_rows[0]["candidate"]["entry_price"] > 0


def test_live_gate_removes_the_entry_only(tmp_path, monkeypatch) -> None:
    eng = _engine(
        tmp_path, monkeypatch,
        entry_gates_enabled=True, entry_gates_shadow_enabled=True,
        entry_gate_max_costs_r=0.0,
    )
    asyncio.run(eng.step())
    assert eng.broker.positions == [], "живой гейт обязан снять вход"
    assert eng.risk._open_positions == {}
    assert any(k.endswith(":live") for k in eng.entry_gate_stats)
    rows = [
        json.loads(line)
        for line in (tmp_path / "obs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [r for r in rows if r.get("rejection_stage") == "entry_gate"], \
        "блокировка пишется в тот же журнал с гипотетическим R"
    # журнал сделок пуст: снятый вход не обязан нигде «всплывать» сделкой
    trades = tmp_path / "state" / "models" / "paper_trades.jsonl"
    if trades.exists():
        assert trades.read_text(encoding="utf-8").strip() == ""
