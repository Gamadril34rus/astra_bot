"""Admin free-text → Grok API (Telegram)."""

from __future__ import annotations

import logging

from telegram import Update

logger = logging.getLogger(__name__)


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
