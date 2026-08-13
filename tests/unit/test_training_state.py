"""Тесты персистентного состояния обучения (живой капитал + стоп + оповещения)."""

from decimal import Decimal
from pathlib import Path

import pytest

from astra_bot.core import training_state as ts_mod
from astra_bot.core.training_state import (
    MIN_CAPITAL,
    TrainingState,
    get_training_state,
    reload_training_state,
)


@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    path = tmp_path / "training_state.json"
    monkeypatch.setenv("TRAINING_STATE_FILE", str(path))
    reload_training_state()
    yield path
    reload_training_state()


def test_default_capital_is_2000(state_file):
    s = TrainingState.load()
    assert s.get_initial_capital() == Decimal("2000")


def test_capital_grows_after_winning_run(state_file):
    s = TrainingState.load()
    nxt = s.record_run(final_equity=Decimal("2500"), trades=10, wins=7, losses=3, pnl=Decimal("500"))
    assert nxt == Decimal("2500")
    # Перечитали с диска — следующий старт 2500, а не 2000.
    again = TrainingState.load()
    assert again.get_initial_capital() == Decimal("2500")


def test_capital_shrinks_after_loss_but_not_below_floor(state_file):
    s = TrainingState.load()
    s.record_run(final_equity=Decimal("300"), trades=10, wins=1, losses=9, pnl=Decimal("-1700"))
    again = TrainingState.load()
    # Упали до 300, но следующий старт зажат минимумом 500.
    assert again.get_initial_capital() == MIN_CAPITAL


def test_capital_capped_by_max(state_file, monkeypatch):
    monkeypatch.setenv("TRAINING_MAX_CAPITAL", "5000")
    s = TrainingState.load()
    s.record_run(final_equity=Decimal("999999"), trades=1, wins=1, losses=0, pnl=Decimal("999799"))
    assert TrainingState.load().get_initial_capital() == Decimal("5000")


def test_stop_request_roundtrip(state_file):
    s = TrainingState.load()
    assert s.should_stop() is False
    s.request_stop()
    # should_stop перечитывает с диска, поэтому видит флаг даже из другого процесса.
    assert TrainingState.load().should_stop() is True
    TrainingState.load().clear_stop()
    assert TrainingState.load().should_stop() is False


def test_quiet_hours_validation(state_file):
    s = TrainingState.load()
    s.set_quiet_hours("23:00", "08:00")
    with pytest.raises(ValueError):
        s.set_daily_report_time("9:00")  # %H:%M требует ведущего нуля
    with pytest.raises(ValueError):
        s.set_quiet_hours("25:00", "08:00")


def test_daily_report_time_persists(state_file):
    s = TrainingState.load()
    s.set_daily_report_time("08:30")
    assert TrainingState.load().daily_report_time == "08:30"


def test_stats_accumulate(state_file):
    s = TrainingState.load()
    s.record_run(final_equity=Decimal("2100"), trades=5, wins=3, losses=2, pnl=Decimal("100"))
    s.record_run(final_equity=Decimal("2000"), trades=5, wins=2, losses=3, pnl=Decimal("-100"))
    st = TrainingState.load().stats
    assert st.runs == 2
    assert st.total_trades == 10
    assert st.wins == 5
    assert st.losses == 5
