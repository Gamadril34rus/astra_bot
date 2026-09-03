"""Тесты бюджета торговых часов."""

from datetime import datetime, timedelta, timezone

from astra_bot.core import trading_schedule as ts

MSK = timezone(timedelta(hours=3))


def test_hours_per_day_divides_month_budget(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_BUDGET_FILE", str(tmp_path / "b.json"))
    monkeypatch.setenv("TRADE_HOURS_PER_MONTH", "700")
    # Август = 31 день -> 22.58 ч/сутки.
    aug = datetime(2026, 8, 13, 12, 0, tzinfo=MSK)
    hpd = ts.hours_per_day(aug)
    assert round(hpd, 1) == round(700 / 31, 1)


def test_cannot_trade_outside_active_hours(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_BUDGET_FILE", str(tmp_path / "b.json"))
    # 03:00 МСК — тонкий рынок, вне активных часов.
    night = datetime(2026, 8, 13, 0, 30, tzinfo=MSK)
    assert ts.can_trade_now(night) is False
    # 15:00 МСК — ликвидная сессия.
    day = datetime(2026, 8, 13, 15, 0, tzinfo=MSK)
    assert ts.can_trade_now(day) is True


def test_record_minutes_persists_and_caps(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_BUDGET_FILE", str(tmp_path / "b.json"))
    monkeypatch.setenv("TRADE_HOURS_PER_MONTH", "1")  # 60 минут
    now = datetime(2026, 8, 13, 10, 0, tzinfo=MSK)
    remaining = ts.record_minutes(40, now)
    assert round(remaining, 1) == round(20 / 60, 1)
    ts.record_minutes(100, now)  # превышение должно закапиться
    st = ts.get_status(now)
    assert st["used_hours"] == 1.0
    assert st["remaining_hours"] == 0.0


def test_daily_budget_resets_next_day(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_BUDGET_FILE", str(tmp_path / "b.json"))
    monkeypatch.setenv("TRADE_HOURS_PER_MONTH", "700")
    d1 = datetime(2026, 8, 13, 23, 0, tzinfo=MSK)
    ts.record_minutes(60, d1)
    d2 = datetime(2026, 8, 14, 10, 0, tzinfo=MSK)
    # Новый день — весь дневной лимит снова доступен.
    assert ts.remaining_minutes_today(d2) == ts.hours_per_day(d2) * 60


def test_new_month_resets_budget(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_BUDGET_FILE", str(tmp_path / "b.json"))
    monkeypatch.setenv("TRADE_HOURS_PER_MONTH", "700")
    aug = datetime(2026, 8, 31, 23, 0, tzinfo=MSK)
    ts.record_minutes(100, aug)
    sep = datetime(2026, 9, 1, 10, 0, tzinfo=MSK)
    st = ts.get_status(sep)
    assert st["month"] == "2026-09"
    assert st["used_hours"] == 0.0
