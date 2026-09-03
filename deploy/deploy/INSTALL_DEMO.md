# ASTRA: 5 лет обучения → непрерывный BingX Spot / paper (ретир OKX)

## 1. Подготовить окружение

```bash
cd /opt/astra_bot
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt python-dotenv
cp .env.example .env
chmod 600 .env
```

В `.env` обязательно:

```dotenv
ENVIRONMENT=paper
PAPER_TRADING=true
# BingX Spot API — опционально для paper (публичные данные без ключей)
BINGX_API_KEY=...
BINGX_API_SECRET=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_ID=...
```

Новостной слой работает **бесплатно и без ключей**:

- основной источник — **GDELT DOC 2.0** (текущие новости и архив ~3 месяца);
- дополнительный — **Free Crypto News API** (cryptocurrency.cv), с безопасным
  fallback: если источник недоступен, ASTRA использует только GDELT, а если
  недоступны оба — нейтральный сентимент (торговля не блокируется).

Платный `NEWS_API_KEY` больше не нужен и не читается. Отключить
дополнительный источник можно через `FREE_CRYPTO_NEWS_ENABLED=0`.

> GDELT DOC официально отдаёт ~3 месяца архива. Для более старых периодов
> пятилетнего прогона новостные признаки остаются нейтральными — это
> осознанный бесплатный компромисс, а не ошибка.

## 2. Пятилетнее обучение

```bash
source /opt/astra_bot/venv/bin/activate
cd /opt/astra_bot
python scripts/strategy_lab.py
```

Пайплайн:

- до 5 лет 1h OHLCV по 35 известным монетам;
- 4h и 1d данные строятся из той же истории;
- несколько тысяч виртуальных сделок walk-forward;
- каждая сделка сохраняется в `models/lessons*.jsonl`;
- новости добавляются в признаки из бесплатных GDELT + Free Crypto News API (без ключей);
- итоговая LightGBM сохраняется в `models/current.pkl`.

По монетам, появившимся менее 5 лет назад, используется вся доступная история, а не выдуманные свечи.

## 3. Запуск непрерывного Demo Trader

```bash
sudo cp deploy/astra-demo-trader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now astra-demo-trader.service
sudo systemctl status astra-demo-trader.service
journalctl -u astra-demo-trader.service -f
```

Worker:

- работает беспрерывно;
- сканирует весь доступный 35-coin universe;
- учитывает ML + графические признаки + news sentiment;
- торгует на данных BingX Spot (paper-контур);
- использует не более 50% общей стоимости demo-счёта;
- оставляет 50% капитала резервом;
- максимум 8 собственных открытых позиций;
- фиксирует каждую закрытую сделку как урок;
- после каждых 200 новых уроков делает дообучение.

Ранее demo-торговля OKX требовала заголовок `x-simulated-trading: 1` и `OKX_DEMO=1`; после ретира OKX → BingX worker работает на бумажном контуре с данными BingX (ключи BingX не обязательны).

## 4. Единственный Telegram-отчёт

```bash
sudo cp deploy/astra-demo-report.service /etc/systemd/system/
sudo cp deploy/astra-demo-report.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now astra-demo-report.timer
sudo systemctl list-timers astra-demo-report.timer
```

Отчёт отправляется один раз в день в **09:00 МСК (06:00 UTC)** и содержит:

```text
ASTRA — утренний отчёт

Сделок за сутки: N
В плюс: N
В минус: N
PnL за сутки: X USDT

Открытых позиций: N
Всего сделок: N
Общий PnL: X USDT
```

Других автоматических Telegram-отчётов этот контур не отправляет.
