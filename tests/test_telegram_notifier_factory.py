"""Блок M: нотифаер движка зовёт существующий bot.send_alert с severity."""

from __future__ import annotations

import scripts.run_bot as rb


class _Bot:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def send_alert(self, message: str, severity: str = "info"):
        self.sent.append((message, severity))


async def test_notifier_uses_send_alert_with_severity():
    bot = _Bot()
    notify = rb.make_telegram_notifier(bot)
    await notify("HALT!", severity="critical")
    assert bot.sent == [("HALT!", "critical")]


async def test_notifier_noop_without_send_alert():
    notify = rb.make_telegram_notifier(object())
    await notify("x")  # чужой объект — тихо ничего не делаем, не падаем


async def test_notifier_swallows_send_errors():
    class _Boom(_Bot):
        async def send_alert(self, message: str, severity: str = "info"):
            raise RuntimeError("net down")

    notify = rb.make_telegram_notifier(_Boom())
    await notify("x")  # ошибка сети не валит сессию
