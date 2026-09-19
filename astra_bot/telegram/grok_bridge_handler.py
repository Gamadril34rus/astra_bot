"""Admin free-text → Grok API (Telegram bridge).

Подключается из ``scripts/run_bot.py`` после create_telegram_bot:
перехватывает текстовые сообщения админа, не ломая меню/команды.
"""

from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

logger = logging.getLogger(__name__)

# Кнопки меню — не отправлять в Grok
_MENU_LABELS = {
    "🎓 Обучение",
    "⏹ Стоп",
    "💰 Баланс",
    "⏰ Настройки",
    "📊 Статус",
    "📈 Отчёт",
    "📍 Позиции",
    "🛡️ Риск",
    "🏥 Здоровье",
    "⏰ Расписание",
    "🎯 Готовность",
    "⚙️ Счёт",
    "❓ Помощь",
}


async def handle_admin_chat(update: Update, text: str) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else 0
    body = text
    low = text.lower().strip()
    for prefix in ("грок:", "grok:", "грок ", "grok ", "@grok "):
        if low.startswith(prefix):
            body = text[len(prefix) :].strip()
            break
    if not body:
        await update.message.reply_text(
            "Напиши сообщение — отвечу как Grok (нужен секрет XAI_API_KEY)."
        )
        return
    wait = await update.message.reply_text("… думаю")
    try:
        from .grok_client import chat as grok_chat

        reply = await grok_chat(body, chat_id=int(chat_id))
    except Exception as exc:
        logger.exception("grok bridge")
        reply = f"⚠️ Мост Grok: {exc}"
    try:
        await wait.edit_text(reply[:4000])
    except Exception:
        await update.message.reply_text(reply[:4000])


def install_on(bot: Any) -> None:
    """Обернуть _handle_text: свободный текст админа → Grok."""
    if getattr(bot, "_grok_bridge_installed", False):
        return
    orig = bot._handle_text

    async def _handle_text_grok(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        user = update.effective_user
        if user is None or update.message is None:
            return
        user_id = user.id
        if not bot._is_allowed(user_id):
            return
        text = (update.message.text or "").strip()
        if not text:
            return

        # Команды / меню / тест — как раньше
        if (
            text.startswith("/")
            or text in _MENU_LABELS
            or text.lower() in {"test", "тест"}
        ):
            await orig(update, context)
            return

        if bot._is_admin(user_id):
            await handle_admin_chat(update, text)
            return

        await orig(update, context)

    bot._handle_text = _handle_text_grok

    # Handlers already bound to old method — re-register
    app = bot._application
    if app is not None:
        try:
            # group 1 runs after default group 0? Use group=-1 to run first
            # Safer: remove and add with new callback
            handlers = list(app.handlers.get(0, []))
            for h in handlers:
                if isinstance(h, MessageHandler):
                    try:
                        app.remove_handler(h)
                    except Exception:
                        pass
            app.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot._handle_text)
            )
        except Exception as exc:
            logger.warning("rebind MessageHandler failed: %s", exc)

    bot._grok_bridge_installed = True
    logger.info("Grok Telegram bridge installed (admin free-text → xAI)")
