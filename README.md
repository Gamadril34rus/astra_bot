# ASTRA BOT — Autonomous Crypto Trading Platform

**Risk-First Quantitative Trading System**

---

## 📋 Описание

ASTRA BOT — автономная криптовалютная торговая система, которая:

- 📊 Анализирует рыночные данные в реальном времени
- 🧠 Использует ML для оценки вероятности прибыльных сделок
- 🛡️ Работает по принципу **Risk-First** (сначала безопасность, потом прибыль)
- 🔄 Автоматически адаптируется к режиму рынка
- 📱 Отправляет отчёты и уведомления в Telegram

### Принципы

1. **Сохранение капитала** — главная цель
2. **Контроль риска** — критические лимиты нельзя обойти
3. **Положительное математическое ожидание** — только статистически обоснованные сделки
4. **Стабильность** — надёжность важнее максимальной прибыли

---

## 🚀 Быстрый старт

### 1. Клонирование и установка

```bash
cd /home/user/astra_bot

# Установка зависимостей
pip install -r requirements.txt

# Копирование конфигурации
cp config/settings.yaml.example config/settings.yaml
cp .env.example .env
```

### 2. Настройка переменных окружения

Отредактируйте `.env` файл:

```bash
# Обязательно для торговли
OKX_API_KEY=your_api_key
OKX_API_SECRET=your_api_secret
OKX_PASSPHRASE=your_passphrase

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_USER_ID=your_user_id

# База данных
DB_HOST=localhost
DB_NAME=astra_bot
DB_USER=astra
DB_PASSWORD=your_secure_password
```

### 3. Запуск тестов

```bash
PYTHONPATH=/home/user/astra_bot python -m pytest tests/ -v
```

### 4. Предварительная проверка

```bash
python scripts/preflight.py
```

### 5. Запуск

```bash
# В режиме разработки
python -m astra_bot.main --env development

# В paper trading режиме
python -m astra_bot.main --env paper

# В production (реальная торговля)
python -m astra_bot.main --env production
```

---

## 🏗️ Архитектура

```
astra_bot/
├── astra_bot/                    # Python package
│   ├── core/                     # Core components
│   │   ├── config.py             # Configuration
│   │   ├── events.py             # Event-driven architecture
│   │   ├── logger.py             # Logging
│   │   ├── models.py             # Domain models
│   │   ├── state.py              # System state
│   │   └── utils.py              # Utilities
│   │
│   ├── adapters/                 # Exchange adapters
│   │   ├── base.py               # Base exchange interface
│   │   ├── okx/                  # OKX integration
│   │   └── bybit/                # Bybit integration
│   │
│   ├── data/                     # Data layer
│   │   ├── database.py           # PostgreSQL
│   │   └── collectors/           # Data collectors
│   │
│   ├── engines/                  # Trading engines
│   │   ├── regime_detector.py    # Market regime detection
│   │   ├── risk_engine.py        # Risk management
│   │   └── execution_engine.py   # Order execution
│   │
│   ├── strategies/               # Trading strategies
│   │   ├── base.py               # Base strategy
│   │   ├── momentum.py           # Trend following
│   │   ├── mean_reversion.py     # Range trading
│   │   └── adaptive_grid.py      # Grid strategy
│   │
│   ├── backtester/               # Backtesting
│   │   ├── engine.py             # Event-driven backtester
│   │   └── analyzer.py           # Results analysis
│   │
│   ├── paperengine/              # Paper trading
│   │   ├── paper_engine.py       # Paper trading engine
│   │   └── simulator.py          # Market simulator
│   │
│   ├── ml/                       # Machine Learning
│   │   ├── feature_pipeline.py   # Feature engineering
│   │   ├── model_trainer.py      # Model training
│   │   ├── predictor.py          # Prediction service
│   │   ├── model_registry.py     # Model versioning
│   │   └── drift_detector.py     # Drift detection
│   │
│   └── telegram/                 # Telegram bot
│       └── bot.py                # Bot implementation
│
├── tests/                        # Tests
│   ├── unit/                     # Unit tests
│   └── integration/              # Integration tests
│
├── config/                       # Configuration
│   └── settings.yaml             # Main config
│
├── monitoring/                   # Monitoring
│   ├── prometheus.yml            # Prometheus config
│   └── grafana/                  # Grafana dashboards
│
├── scripts/                      # Scripts
│   ├── start.sh                  # Startup script
│   ├── preflight.py              # Pre-flight check
│   ├── daily_report.py           # Daily report
│   └── backup.sh                 # Backup script
│
├── docker-compose.yml            # Docker deployment
├── Dockerfile                    # Container build
├── init.sql                      # Database schema
└── README.md                     # This file
```

---

## 📊 Trading Strategies

### 1. Momentum Strategy
- **Тип:** Trend-following
- **Логика:** EMA20 > EMA50 > EMA200 + volume confirmation
- **Режимы:** BULL_TREND, BREAKOUT, LOW_VOLATILITY
- **Stop:** ATR-based (1.5 × ATR)
- **Take Profit:** 1R, 2R, 3R levels

### 2. Mean Reversion Strategy
- **Тип:** Range trading
- **Логика:** Bollinger Bands + RSI + Z-score
- **Режимы:** RANGE, LOW_VOLATILITY
- **Stop:** 2% от цены
- **Take Profit:** Возврат к средней линии BB

### 3. Adaptive Grid Strategy
- **Тип:** Grid trading
- **Логика:** Сетевая стратегия с адаптацией к волатильности
- **Режимы:** ТОЛЬКО RANGE
- **⚠️ ЗАПРЕЩЕНО:** Martingale, Averaging Down

---

## 🛡️ Risk Management

### Параметры по умолчанию

| Параметр | Значение |
|----------|----------|
| Risk per trade | 0.4% |
| Daily loss limit | 2% |
| Weekly loss limit | 4% |
| Soft drawdown | 5% |
| Hard drawdown | 8% |
| Emergency drawdown | 10% |
| Max exposure | 30% |
| Max positions | 5 |

### Drawdown Adaptation

| Просадка | Множитель риска |
|----------|-----------------|
| 0-3% | 1.0 (норма) |
| 3-5% | 0.75 (снижен) |
| 5-8% | 0.5 (оборонительный) |
| 8%+ | 0.0 (стоп) |

---

## 🐳 Docker Deployment

### 1. Подготовка

```bash
# Создайте .env файл
cp .env.example .env
# Отредактируйте .env с вашими значениями

# Создайте директории
mkdir -p data logs models backups
```

### 2. Запуск

```bash
# Запуск всех сервисов
docker-compose up -d

# Проверка статуса
docker-compose ps

# Просмотр логов
docker-compose logs -f astra_bot
```

### 3. Сервисы

| Сервис | Порт | Описание |
|--------|------|---------|
| astra_bot | 8000 | Основное приложение |
| postgres | 5432 | База данных |
| redis | 6379 | Кэш |
| prometheus | 9090 | Метрики |
| grafana | 3000 | Дашборды |

---

## 📱 Telegram Commands

| Команда | Описание | Доступ |
|---------|----------|--------|
| `/start` | Приветствие | Все |
| `/status` | Текущий статус | Все |
| `/report` | Детальный отчёт | Все |
| `/positions` | Открытые позиции | Все |
| `/risk` | Риск-статус | Все |
| `/health` | Здоровье системы | Все |
| `/pause` | Приостановить торговлю | Admin |
| `/resume` | Возобновить торговлю | Admin |

---

## ⚠️ Важные предупреждения

### 1. Безопасность API ключей

```yaml
# OKX API ключи ДОЛЖНЫ иметь:
# - Read: YES
# - Trade: YES
# - Withdraw: NO  ← ОБЯЗАТЕЛЬНО!
```

### 2. Минимальный капитал

При капитале **1 000 ₽** и риске **0.4%**:
- Допустимый риск = 4 ₽
- Минимальный ордер OKX может быть 10 USDT

**Рекомендуемый стартовый капитал:** 5 000-10 000 ₽

### 3. Реальные ожидания

Цель 1 000 → 5 000 ₽:
- При 0.3%/день: ~12 месяцев
- При 0.5%/день: ~8 месяцев

**Это не гарантия!** Результаты будут отличаться.

---

## 🔄 Стадии разработки

- [x] **V0.1** — Market Data + PostgreSQL
- [x] **V0.2** — Backtester
- [x] **V0.3** — Momentum Strategy
- [x] **V0.4** — Mean Reversion Strategy
- [x] **V0.5** — Risk Engine
- [x] **V0.6** — Paper Trading
- [x] — Telegram Bot (в коде)
- [ ] **V0.7** — Exchange Execution (OKX реальная)
- [ ] **V0.8** — ML Engine
- [ ] **V1.0** — Production Ready

---

## 📊 Тесты

```bash
# Все тесты
PYTHONPATH=/home/user/astra_bot python -m pytest tests/ -v

# Только unit тесты
PYTHONPATH=/home/user/astra_bot python -m pytest tests/unit/ -v

# Только интеграционные
PYTHONPATH=/home/user/astra_bot python -m pytest tests/integration/ -v
```

**Статус:** 114 тестов pass ✅

---

**Статус:** 114 тестов pass ✅

## 📋 Чек-лист запуска

### Подготовка VPS

```bash
# 1. Установка зависимостей
sudo apt update && sudo apt install -y python3.12 python3.12-venv postgresql redis-server

# 2. Создание пользователя
sudo useradd -m -s /bin/bash botuser
sudo passwd botuser

# 3. Клонирование и настройка
su - botuser
cd ~
git clone <repo-url> astra_bot
cd astra_bot

# 4. Виртуальное окружение
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 5. База данных
sudo -u postgres psql -c "CREATE USER astra WITH PASSWORD 'your_password';"
sudo -u postgres psql -c "CREATE DATABASE astra_bot OWNER astra;"
```

### Настройка биржи

1. Зайдите в OKX → API Management
2. Создайте API ключ с правами:
   - ✅ Read
   - ✅ Trade
   - ❌ Withdraw (ВАЖНО!)
3. Скопируйте ключи в `.env`

### Настройка Telegram

1. Напишите @BotFather → `/newbot`
2. Получите токен
3. Напишите @userinfobot → получите свой User ID
4. Добавьте в `.env`

### Первый запуск

```bash
# Предварительная проверка
python scripts/preflight.py

# Старт
python -m astra_bot.main --env paper
```

### Мониторинг

```bash
# Статус системы
python -m astra_bot.main --action status

# Генерация отчёта
python scripts/daily_report.py

# Проверка логов
tail -f logs/astra_bot.log
```

---

## 📄 Лицензия

MIT License

---

## 🔗 Ссылки

- [OKX API Documentation](https://www.okx.com/docs-v5/)
- [Telegram Bot API](https://core.telegram.org/bots/api)
