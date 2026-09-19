# Мост Grok ↔ Telegram (xAI API)

## Что это

Свободный текст **админа** в чате ASTRA-бота уходит в **xAI Grok API**
(`https://api.x.ai/v1/chat/completions`), ответ приходит в тот же чат.

Кнопки меню и команды (`/balance`, `/status`, …) работают как раньше.

## Что нужно от владельца (один раз)

1. Ключ: [console.x.ai → API Keys](https://console.x.ai/team/default/api-keys)
2. GitHub → Settings → Secrets → Actions → **`XAI_API_KEY`** = ключ
3. Merge PR с мостом; следующий `bot.yml` session (~5 мин) подхватит секрет

Опционально: `XAI_MODEL` (по умолчанию `grok-4`).

## Как писать

Просто текст в личку боту (с аккаунта admin id).  
Или с префиксом: `грок что с zeus journal?`

Окно ответа: бот слушает ~200 с каждые 5 мин — если сообщение ушло
в «тишину» между сессиями, дождись следующего окна или напиши снова.

## Файлы

- `astra_bot/telegram/grok_client.py` — HTTP к xAI + история
- `astra_bot/telegram/grok_bridge_handler.py` — перехват текста
- `models/grok_tg_chat.jsonl` — короткая история диалога (коммитится)

## Не трогает

Live, risk%, settings.yaml, торговые пути.
