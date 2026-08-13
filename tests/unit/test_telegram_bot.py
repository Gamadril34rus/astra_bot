"""Тесты Telegram-бота: русское меню и выбор счёта."""

import pytest
from astra_bot.telegram.bot import (
    BOT_COMMANDS,
    COMMAND_ALIASES,
    MAIN_MENU,
    AstraTelegramBot,
    account_mode_keyboard,
)


@pytest.fixture()
def bot() -> AstraTelegramBot:
    return AstraTelegramBot(
        bot_token="test-token",
        allowed_user_ids=[111],
        admin_user_ids=[222],
    )


def test_bot_starts_in_paper_mode(bot):
    assert bot.account_mode == "paper"
    assert bot.real_trading_confirmed is False


def test_account_keyboard_marks_current_mode():
    kb = account_mode_keyboard("paper")
    # Кнопка демо помечена галочкой, реальной — нет.
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("✅" in t and "Демо" in t for t in texts)


def test_main_menu_is_in_russian():
    labels = [btn.text for row in MAIN_MENU.keyboard for btn in row]
    assert "📊 Статус" in labels
    assert "📈 Отчёт" in labels
    assert "⚙️ Счёт" in labels
    assert "❓ Помощь" in labels


def test_admin_required_for_real_trading_confirmation(bot):
    # Обычный пользователь не может подтвердить реальную торговлю.
    assert bot._is_admin(111) is False
    assert bot._is_admin(222) is True


def test_money_formatter_handles_none(bot):
    assert "0.00" in bot._fmt_money(None)
    assert "₽" in bot._fmt_money(1234.5)


def test_russian_command_menu_has_required_commands():
    # Служебные команды — латинские (Telegram не принимает кириллицу в
    # BotCommand), но с русскими описаниями.
    names = {c for c, _ in BOT_COMMANDS}
    for required in ("train", "stop", "balance", "settings"):
        assert required in names, f"нет команды {required}"
    descriptions = {c: d for c, d in BOT_COMMANDS}
    assert "обучение" in descriptions["train"].lower()
    assert "баланс" in descriptions["balance"].lower()
    # Русские вводы распознаются как алиасы:
    from astra_bot.telegram.bot import RUSSIAN_ALIASES
    assert RUSSIAN_ALIASES["обучение"] == "train"
    assert RUSSIAN_ALIASES["стоп"] == "stop"
    assert RUSSIAN_ALIASES["баланс"] == "balance"
    assert RUSSIAN_ALIASES["настройки"] == "settings"


def test_main_menu_contains_new_buttons():
    labels = [btn.text for row in MAIN_MENU.keyboard for btn in row]
    for label in ("🎓 Обучение", "⏹ Стоп", "💰 Баланс", "⏰ Настройки"):
        assert label in labels


def test_settings_text_renders(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAINING_STATE_FILE", str(tmp_path / "ts.json"))
    from astra_bot.core.training_state import reload_training_state
    reload_training_state()
    bot = AstraTelegramBot(bot_token="x", allowed_user_ids=[1], admin_user_ids=[2])
    from astra_bot.core.training_state import get_training_state
    ts = get_training_state()
    ts.set_daily_report_time("08:30")
    ts.set_alerts(True)
    text = bot._settings_text(ts)
    assert "08:30" in text
    assert "ВКЛ" in text
