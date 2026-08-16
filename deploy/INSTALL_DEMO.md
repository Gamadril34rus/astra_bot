# ASTRA: 5 лет обучения → непрерывный OKX Demo

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
OKX_DEMO=1
OKX_API_KEY=...
OKX_API_SECRET=...
OKX_API_PASSPHRASE=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_ID=...
```

Для полноценного исторического новостного слоя дополнительно:

```dotenv
NEWS_API_KEY=...
```

Для полноценного пятилетнего исторического корпуса новостей нужен тариф источника, который предоставляет такую глубину истории.

## 2. Пятилетнее обучение

```bash
source /opt/astra_bot/venv/bin/activate
cd /opt/astra_bot
python scripts/pretrain_5y.py --years 5 --target-trades 5000 --min-samples 2000 --with-news
```

Пайплайн:

- до 5 лет 1h OHLCV по 35 известным монетам;
- 4h и 1d данные строятся из той же истории;
- несколько тысяч виртуальных сделок walk-forward;
- каждая сделка сохраняется в `models/lessons*.jsonl`;
- новости добавляются в признаки, когда `NEWS_API_KEY` доступен;
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
- торгует только OKX Demo;
- использует не более 50% общей стоимости demo-счёта;
- оставляет 50% капитала резервом;
- максимум 8 собственных открытых позиций;
- фиксирует каждую закрытую сделку как урок;
- после каждых 200 новых уроков делает дообучение.

OKX Demo Trading требует специальный заголовок `x-simulated-trading: 1`; worker запускается только с `OKX_DEMO=1`.

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
