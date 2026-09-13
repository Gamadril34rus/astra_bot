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


def test_can_trade_at_night_24_7(monkeypatch, tmp_path):
    """24/7 (решение владельца 13.09.2026): ночью вход разрешён."""
    monkeypatch.setenv("TRADING_BUDGET_FILE", str(tmp_path / "b.json"))
    monkeypatch.delenv("TRADE_ACTIVE_HOURS_MSK", raising=False)
    # 03:00 МСК — раньше было «вне активных часов», теперь разрешено.
    night = datetime(2026, 8, 13, 3, 0, tzinfo=MSK)
    assert ts.can_trade_now(night) is True
    # 00:30 и 15:00 — тоже разрешены.
    assert ts.can_trade_now(datetime(2026, 8, 13, 0, 30, tzinfo=MSK)) is True
    assert ts.can_trade_now(datetime(2026, 8, 13, 15, 0, tzinfo=MSK)) is True


def test_active_hours_env_override_restricts_night(monkeypatch, tmp_path):
    """TRADE_ACTIVE_HOURS_MSK сужает окно (старое поведение по желанию)."""
    monkeypatch.setenv("TRADING_BUDGET_FILE", str(tmp_path / "b.json"))
    monkeypatch.setenv("TRADE_ACTIVE_HOURS_MSK", "8-23")
    assert ts.can_trade_now(datetime(2026, 8, 13, 3, 0, tzinfo=MSK)) is False
    assert ts.can_trade_now(datetime(2026, 8, 13, 15, 0, tzinfo=MSK)) is True
    # Мусор в переменной — fail-open к 24/7, а не падение.
    monkeypatch.setenv("TRADE_ACTIVE_HOURS_MSK", "не часы")
    assert ts.can_trade_now(datetime(2026, 8, 13, 3, 0, tzinfo=MSK)) is True


def test_budget_covers_full_31_day_month_24_7(monkeypatch, tmp_path):
    """750 ч покрывают 31-дневный месяц 24/7 (744 ч): бот не встаёт."""
    monkeypatch.setenv("TRADING_BUDGET_FILE", str(tmp_path / "b.json"))
    monkeypatch.delenv("TRADE_HOURS_PER_MONTH", raising=False)
    monkeypatch.delenv("TRADE_ACTIVE_HOURS_MSK", raising=False)
    assert ts.DEFAULT_HOURS_PER_MONTH == 750
    aug = datetime(2026, 8, 1, 12, 0, tzinfo=MSK)
    assert ts.hours_per_day(aug) * 60 >= 24 * 60  # дневной лимит >= суток
    # Прожигаем полные сутки 31 день подряд.
    for day in range(1, 32):
        ts.record_minutes(24 * 60, datetime(2026, 8, day, 12, 0, tzinfo=MSK))
    last = datetime(2026, 8, 31, 23, 59, tzinfo=MSK)
    assert ts.can_trade_now(last) is True
    st = ts.get_status(last)
    assert st["remaining_hours"] == 750 - 744


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
