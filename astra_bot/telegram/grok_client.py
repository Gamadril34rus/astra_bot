"""Мост Telegram → xAI Grok API (OpenAI-compatible chat completions).

Требует секрет ``XAI_API_KEY`` (console.x.ai).
Не трогает live-торговлю: только текст ответа в чат.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

XAI_BASE = os.environ.get("XAI_API_BASE", "https://api.x.ai/v1").rstrip("/")
DEFAULT_MODEL = os.environ.get("XAI_MODEL", "grok-4")
HISTORY_PATH = Path(
    os.environ.get("GROK_TG_HISTORY", "models/grok_tg_chat.jsonl")
)
MAX_HISTORY_TURNS = 12
MAX_REPLY_CHARS = 3500

SYSTEM_PROMPT = """Ты Grok (xAI), координатор research paper-бота astra (репозиторий владельца).
Отвечай по-русски, кратко и по делу, удобно читать с телефона.

Правила до среза 26–27.09.2026:
- Live-контур заморожен: не предлагай включать live, поднимать risk%,
  менять settings.yaml / bot.yml / risk_engine без явного решения владельца.
- Zeus paper-clock — тень/paper; сделки только paper.
- Команды ASTRA-бота в Telegram (баланс, статус, позиции) остаются отдельными;
  ты — живой разбор, гипотезы, что смотреть в journal.

Если не хватает данных из репо — скажи, что проверить.
Не выдумывай PnL и сделки."""


def _api_key() -> str:
    return (
        os.environ.get("XAI_API_KEY", "").strip()
        or os.environ.get("GROK_API_KEY", "").strip()
    )


def is_configured() -> bool:
    return bool(_api_key())


def _load_history(chat_id: int) -> list[dict[str, str]]:
    if not HISTORY_PATH.exists():
        return []
    rows: list[dict[str, str]] = []
    try:
        for line in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if int(r.get("chat_id") or 0) != int(chat_id):
                continue
            role = r.get("role")
            content = r.get("content")
            if role in ("user", "assistant") and content:
                rows.append({"role": role, "content": str(content)})
    except Exception as exc:
        logger.debug("grok history load: %s", exc)
        return []
    return rows[-(MAX_HISTORY_TURNS * 2) :]


def _append_history(chat_id: int, role: str, content: str) -> None:
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY_PATH.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {"chat_id": chat_id, "role": role, "content": content},
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception as exc:
        logger.debug("grok history append: %s", exc)


async def chat(user_text: str, chat_id: int) -> str:
    """Один ход диалога. Возвращает текст ответа или сообщение об ошибке."""
    key = _api_key()
    if not key:
        return (
            "⚠️ Мост Grok не настроен: добавь секрет **XAI_API_KEY** "
            "в GitHub Actions (ключ с https://console.x.ai )."
        )

    history = _load_history(chat_id)
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    url = f"{XAI_BASE}/chat/completions"
    payload = {
        "model": DEFAULT_MODEL,
        "messages": messages,
        "temperature": 0.5,
        "max_tokens": 1200,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code >= 400:
            body = resp.text[:300]
            logger.warning("xAI API %s: %s", resp.status_code, body)
            return (
                f"⚠️ xAI API ошибка {resp.status_code}. "
                f"Проверь ключ/квоту. ({body[:120]})"
            )
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return "⚠️ Пустой ответ модели."
        reply = (choices[0].get("message") or {}).get("content") or ""
        reply = str(reply).strip() or "…"
        if len(reply) > MAX_REPLY_CHARS:
            reply = reply[: MAX_REPLY_CHARS - 1] + "…"

        _append_history(chat_id, "user", user_text[:2000])
        _append_history(chat_id, "assistant", reply[:2000])
        return reply
    except httpx.TimeoutException:
        return "⚠️ xAI API timeout — попробуй ещё раз."
    except Exception as exc:
        logger.exception("grok chat failed")
        return f"⚠️ Сбой моста Grok: {exc}"
