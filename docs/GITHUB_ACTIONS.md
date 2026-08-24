# Запуск на GitHub Actions — бесплатно, без карты и ПК

Бот будет работать на серверах GitHub. Карта и адрес не нужны.
Подходит для обучения и paper trading — реальные деньги не включать.

## Стоимость

- **Публичный репозиторий** — минуты не тратятся вообще.
- Приватный — 2000 минут/мес, у нас уходит ~690.

| Workflow | Когда | Минут/мес |
|---|---|---:|
| `strategy-lab.yml` | каждый понедельник в 08:00 UTC | ~300 |
| `paper-trade.yml` | каждый час в :07 | ~240 |

## Одноразовая настройка

1. Сделайте репозиторий **публичным**:
   Settings → General → Danger Zone → Change visibility.

2. **Отзовите старый OKX API-ключ** (он был в публичной истории git).
   Создайте новый **demo-trading** ключ с правами Read + Trade, без Withdraw.
   IP whitelist оставьте пустым.

3. Добавьте секреты (Settings → Secrets and variables → Actions):

   | Имя | Значение |
   |---|---|
   | `OKX_API_KEY` | demo API key |
   | `OKX_API_SECRET` | demo API secret |
   | `OKX_API_PASSPHRASE` | passphrase |
   | `TELEGRAM_BOT_TOKEN` | токен от @BotFather (необязательно) |
   | `TELEGRAM_ADMIN_ID` | ваш ID от @userinfobot (необязательно) |

4. Запустите первое обучение вручную:
   - вкладка **Actions**;
   - слева **Daily train + report**;
   - справа **Run workflow** → Run.

## Как работает

- В 08:00 МСК GitHub поднимает Ubuntu, качает 3 года свечей OKX,
  прогоняет self-play на 15m/1h/4h/1d, обучает LightGBM, шлёт
  утренний отчёт в Telegram и коммитит модель и lessons в ветку.
- Каждый час запускается `paper-trade.yml`, делает один цикл
  через `run_paper.py --once` и сохраняет состояние позиций.
- Между запусками состояние живёт в `models/` в git.

## Проверка

- Вкладка **Actions** показывает все запуски и логи.
- В 09:00 МСК приходит отчёт в Telegram.
- История сделок — в файле `models/paper_trades.jsonl`.

## Ограничения

- Один запуск максимум 45 мин.
- Нет состояния в памяти между запусками — всё пишется в файлы.
- Очередь GitHub Actions может задержать запуск на пару минут
  (для 1h таймфрейма это не критично).
- Только paper trading.

## Выключение

- Actions → выбрать workflow → «…» → Disable workflow.
- Или удалить OKX-секреты — джоба упадёт на шаге Connection test.
