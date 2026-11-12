"""Блок M: trade notifier отправляет карточки через admin-only канал, не ASTRA ALERT."""

from __future__ import annotations

import scripts.run_bot as rb


class _Bot:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def _send_to_admins(self, message: str, severity: str = "info"):
        self.sent.append((message, severity))


async def test_notifier_uses_admin_channel_with_severity():
    bot = _Bot()
    notify = rb.make_telegram_notifier(bot)
    await notify("TRADE EXIT", severity="critical")
    assert bot.sent == [("TRADE EXIT", "critical")]


async def test_notifier_noop_without_admin_channel():
    notify = rb.make_telegram_notifier(object())
    await notify("x")  # чужой объект — тихо ничего не делаем, не падаем


async def test_notifier_swallows_send_errors():
    class _Boom(_Bot):
        async def _send_to_admins(self, message: str, severity: str = "info"):
            raise RuntimeError("net down")

    notify = rb.make_telegram_notifier(_Boom())
    await notify("x")  # ошибка сети не валит сессию
