"""Тесты оценки готовности к реальному счёту."""

from datetime import date, timedelta

from astra_bot.core import readiness


def _good_days(n=40):
    base = date(2026, 7, 1)
    days = []
    for i in range(n):
        days.append({
            "date": (base + timedelta(days=i)).isoformat(),
            "trades": 8, "wins": 6, "pnl": 30.0,
            "equity_end": 2000 + i * 20,
        })
    return days


def test_not_ready_when_just_started(tmp_path, monkeypatch):
    monkeypatch.setenv("READINESS_FILE", str(tmp_path / "r.json"))
    readiness.record_day(trades=5, wins=3, pnl=10, equity_end=2010)
    v = readiness.evaluate()
    assert v["ready"] is False
    assert v["trading_days"] == 1


def test_ready_after_consistent_good_history(tmp_path, monkeypatch):
    monkeypatch.setenv("READINESS_FILE", str(tmp_path / "r.json"))
    state = readiness.ReadinessState()
    for d in _good_days(40):
        state.days.append(d)
    state.total_trades = sum(d["trades"] for d in state.days)
    state.total_wins = sum(d["wins"] for d in state.days)
    state.total_pnl = sum(d["pnl"] for d in state.days)
    readiness.save(state)
    v = readiness.evaluate()
    assert v["ready"] is True
    assert v["score"] >= 85
    assert v["win_rate"] >= 55


def test_notify_only_once(tmp_path, monkeypatch):
    monkeypatch.setenv("READINESS_FILE", str(tmp_path / "r.json"))
    state = readiness.ReadinessState()
    for d in _good_days(40):
        state.days.append(d)
    readiness.save(state)
    assert readiness.should_notify_ready() is True
    assert readiness.should_notify_ready() is False  # уже уведомили
