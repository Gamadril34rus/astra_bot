"""Тесты Telegram-бота: русское меню и выбор счёта."""

import pytest
from astra_bot.telegram.bot import (
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
