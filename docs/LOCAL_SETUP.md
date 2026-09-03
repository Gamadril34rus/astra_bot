# ASTRA BOT — Руководство по запуску на ПК

## 1. Что нужно установить

### Windows
1. **Python 3.11** — скачайте с https://www.python.org/downloads/
   При установке поставьте галочку **"Add Python to PATH"**.
2. **Git for Windows** — https://git-scm.com/download/win
3. Откройте **PowerShell** или **cmd**.

### macOS
```bash
# Установите Homebrew (если нет)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
# Python 3.11
brew install python@3.11 git
```

### Linux (Ubuntu/Debian)
```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip git
```

## 2. Скачать проект

```bash
git clone https://github.com/Gamadril/astra_bot.git
cd astra_bot
```

Если репозиторий приватный, используйте SSH или Personal Access Token.

## 3. Создать виртуальное окружение

### Windows (PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
Если PowerShell ругается на политику выполнения:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### macOS / Linux
```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

После активации в начале строки появится `(.venv)`.

## 4. Установить зависимости

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## 5. Настроить переменные окружения

Скопируйте пример и отредактируйте `.env`:

### Windows
```powershell
copy .env.example .env
notepad .env
```

### macOS / Linux
```bash
cp .env.example .env
nano .env
```

Заполните:

```env
# Режим работы
ENVIRONMENT=paper
PAPER_TRADING=true

# BingX Spot API (активная биржа; ретир OKX). Опционально для paper:
# рыночные данные публичны, ключи нужны только для баланса спот-счёта.
BINGX_API_KEY=
BINGX_API_SECRET=

# Telegram (опционально, для отчётов)
TELEGRAM_BOT_TOKEN=токен-от-@BotFather
TELEGRAM_ADMIN_ID=ваш-id-из-@userinfobot
```

> ⚠️ **Безопасность:** файл `.env` уже в `.gitignore` и не попадёт в git.
> Если вы уже когда-то запушили ключи в git — отзовите их и создайте новые.

### Как получить BingX API ключи (опционально)
Для paper-контура ключи не обязательны: рынок BingX публичен. Ключи нужны
только чтобы команда `/баланс` показывала реальный спот-баланс BingX.
1. Зайдите на https://www.bingx.com/
2. Аккаунт → **API** → **Create API key**
3. Права: **Read** (+ **Trade** — только перед осознанным включением live),
   **Withdraw: NO** (никогда)
4. Скопируйте API Key и API Secret в `.env` (passphrase у BingX нет)
5. IP whitelist можно оставить пустым

## 6. Проверить соединение с BingX

```bash
python scripts/test_bingx.py
```

Ожидаемый вывод:
```
Public endpoint: OK (5 candles)
Private endpoint: пропущен (ключи не заданы)   # если ключей нет
Private endpoint: OK (2 balances)              # если ключи заданы
```

Если видите ошибку соединения с `open-api.bingx.com` — у вас
брандмауэр/провайдер блокирует API BingX. Попробуйте VPN или сервер в
другой стране.

## 7. Обучить бота

### 7.1. Быстрое обучение (1 год, 4 таймфрейма)
```bash
python scripts/train_multi_timeframe.py --days 365
```

### 7.2. Полное обучение (3 года)
```bash
python scripts/train_multi_timeframe.py --days 1095 --target-trades 1000
```

Что произойдёт:
* бот скачает реальные свечи BingX по BTC/ETH/SOL;
* прогонит self-play на 15m/1h/4h/1d;
* сохранит уроки в `models/lessons.jsonl`;
* обучит LightGBM и сохранит в `models/current.pkl`.

Скрипт выведет PnL, win-rate и profit factor по каждому таймфрейму.

### 7.3. (Опционально) Дообучить вручную
```bash
python scripts/train_weekly.py --min-samples 200
```

## 8. Утренние отчёты

Чтобы бот раз в сутки дообучался и слал отчёт в Telegram:

```bash
python scripts/learning_week.py
```

Оставьте процесс работать. В 08:00 МСК бот подтянет новые данные, в 09:00 пришлёт отчёт.

### Как сделать сервис (Linux/systemd)
```bash
sudo nano /etc/systemd/system/astra-learning.service
```

```ini
[Unit]
Description=ASTRA bot daily learner
After=network-online.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/astra_bot
ExecStart=/home/YOUR_USER/astra_bot/.venv/bin/python scripts/learning_week.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now astra-learning.service
sudo systemctl status astra-learning.service
journalctl -u astra-learning.service -f
```

## 9. Запустить paper trading

```bash
python scripts/run_paper.py
```

Опции:
```bash
# Один тестовый цикл и выход
python scripts/run_paper.py --once

# Каждые 60 секунд, кастомные пары
python scripts/run_paper.py --interval 60 --symbols BTC-USDT ETH-USDT
```

Бот:
* каждые 5 минут тянет свечи и стакан;
* прогоняет `DecisionPipeline`;
* открывает виртуальные позиции через `PaperBroker`;
* пишет сделки в `models/paper_trades.jsonl`;
* состояние позиций — в `models/paper_positions.json`.

### Остановить
`Ctrl+C`. Корректно закроется и сохранит состояние.

## 10. Посмотреть результаты

```bash
python scripts/morning_report.py
```

Выведет отчёт по виртуальному счёту и отправит его в Telegram.

## Структура проекта

```
astra_bot/
├── astra_bot/
│   ├── adapters/         # BingX клиент (ретир OKX)
│   ├── core/             # модели, индикаторы
│   ├── decision/         # пайплайн решений (regime, risk, EV)
│   ├── ml/               # обучение, LightGBM
│   ├── paperengine/      # виртуальные позиции
│   ├── strategies/       # PullbackStrategy, Momentum, ...
│   └── telegram/         # бот и кнопки
├── scripts/
│   ├── test_bingx.py          # проверка ключей и сети
│   ├── train_multi_timeframe.py  # обучение
│   ├── train_weekly.py        # дообучение вручную
│   ├── morning_report.py      # отчёт за сутки
│   ├── learning_week.py       # демон-цикл (обучение + отчёт)
│   └── run_paper.py           # live paper trading
├── models/               # артефакты обучения (lessons, current.pkl)
├── tests/                # pytest — 178 тестов
├── docs/                 # подробные спецификации
├── .env                  # ВАШИ КЛЮЧИ — не коммитить
└── requirements.txt
```

## Частые проблемы

### `Cannot connect to host open-api.bingx.com`
BingX API недоступен с вашего IP. Включите VPN или арендуйте VPS в стране, где API BingX работает.

### `ModuleNotFoundError: No module named 'dotenv'`
Вы забыли активировать виртуальное окружение (`source .venv/bin/activate` или `.\.venv\Scripts\Activate.ps1`).

### `Invalid OK-ACCESS-KEY`
Проверьте `.env`: нет ли лишних пробелов/кавычек вокруг ключей.
Убедитесь, что ключи именно **demo-trading**, а не основного аккаунта.

### LightGBM ругается
`pip install lightgbm --upgrade`

### Telegram-бот не шлёт отчёты
* Создайте бота у @BotFather, скопируйте токен в `.env`.
* Свой ID узнайте у @userinfobot.
* Напишите своему боту `/start` — иначе он не сможет вам написать первым.

## Метрики, на которые смотреть

* **Win-rate** — процент прибыльных сделок (цель 55–70%).
* **Profit Factor** — валовая прибыль / валовой убыток. Цель > 1.2.
* **Max Drawdown** — макс. просадка. Стоп при 15%.
* **AUC модели** — 0.5 = монетка, 0.6+ = есть edge.
* **PnL** — итоговая прибыль в %.

Помните: это **paper trading**. Реальные деньги включайте только после 30+ дней стабильной работы с PF > 1.2 и положительным Sharpe.
