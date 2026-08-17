# ASTRA BOT

> **Адаптивная исследовательская и demo-трейдинговая система для криптовалютного рынка.**

ASTRA предназначена для исторического исследования, виртуального обучения, накопления структурированных уроков, ML-обучения и длительной работы в **OKX Demo Trading**. Реальные деньги сейчас отключены, автоматического перехода на реальный счёт нет.

## 🎯 Как работает ASTRA

```text
История рынка
    ↓
Свечи + объём + структура + индикаторы
    ↓
Новости + ликвидность + контекст крупных активов
    ↓
Walk-forward симуляция
    ↓
Виртуальные сделки
    ↓
Результат / PnL / факторы / counterfactual
    ↓
Lesson Memory + Pattern Memory
    ↓
Temporal / out-of-sample validation
    ↓
ML-модель
    ↓
OKX Demo Trading
    ↓
Новые Demo-уроки
    ↓
Переобучение + контроль drift
```

### Рыночный анализ

ASTRA работает не с одним индикатором. В feature/decision pipeline используются свечи и бары, тренды, momentum, HH/HL/LH/LL, уровни, pivots, каналы, пробои и ретесты, Fibonacci-контекст, объём, OBV, VWAP, RSI, Stochastic, CCI, ATR, ADX-подобные признаки, EMA, Bollinger Bands, корреляции, ликвидность/order book, новостной контекст и режим рынка.

Наличие отдельного инструмента в коде не считается доказательством его эффективности. Все признаки должны проверяться в общем временном контуре без утечки будущих данных.

## 🌍 Историческое обучение

Целевой universe содержит около **35 ликвидных USDT-пар**. Перед торговым циклом список сверяется с актуальными SPOT-инструментами OKX, поэтому недоступные пары не должны генерировать ошибки внутри worker.

Исторический learner работает в режиме **MAX_HISTORY**: он пытается использовать максимально доступную историю конкретного инструмента. Для нового токена это может быть меньше пяти лет, для старого — больше. Несуществующие данные не выдумываются.

Запросы к OKX идут через централизованный rate limiter с retry/backoff. Исторический этап не совершает реальные ордера.

## 📚 Память

### Lesson Memory

Закрытые виртуальные и Demo-сделки могут сохраняться как структурированные уроки: инструмент, время, направление, вход/выход, режим рынка, признаки, PnL, повлиявший фактор, counterfactual и рекомендация.

### Pattern Memory

`models/market_memory.json` агрегирует повторяющиеся сочетания условий и хранит количество наблюдений, wins/losses, сглаженный win-rate, PnL и типичные рекомендации.

Память дополняет ML, но не заменяет статистическую проверку.

## 🤖 Demo Trading

| Параметр | Политика |
|---|---:|
| Реальные деньги | **Отключены** |
| Режим | **OKX Demo / paper** |
| Доля выделенного капитала | **50% Demo equity** |
| Резерв | **50%** |
| Максимум позиций | 8 |
| Риск на сделку | 0,4% выделенного капитала |
| Максимальная позиция | 10% выделенного капитала |
| ML threshold | 0,60 |
| Максимальное удержание | 48 ч |
| Circuit breaker | включён |
| Реальный счёт | **не включается автоматически** |

GitHub Actions используется как временная инфраструктура. Worker должен сохранять state/checkpoint, уроки и модель, чтобы следующий runner мог продолжить работу.

## 🛡️ Readiness Gate

ASTRA не обещает невозможного: гарантировать отсутствие убытков на реальном рынке нельзя. Перед рассмотрением реального счёта используется многоступенчатый фильтр.

| Метрика | Базовый порог |
|---|---:|
| Demo history | ≥ 30 торговых дней |
| Закрытые сделки | ≥ 200 |
| Win rate | ≥ 55% |
| Profit factor | ≥ 1,3 |
| Max drawdown | ≤ 8% |
| Прибыльные дни | ≥ 55% |
| Максимальная серия убытков | < 6 дней |
| Sharpe | ≥ 1,0 |
| Readiness score | ≥ 85/100 |

Даже прохождение gate не гарантирует прибыль. Реальный запуск остаётся отдельным ручным решением.

## 📲 Telegram

Основной отчёт отправляется **один раз в день в 09:00 по Москве** и содержит:

```text
ASTRA BOT — утренний отчёт

Сделок: 24
В плюс: 15
В минус: 9
PnL: +38,42 USDT
```

Критические события допускают отдельное уведомление: circuit breaker, потеря связи, ошибка данных, инфраструктурная ошибка или изменение readiness.

## 🏗️ Структура проекта

```text
astra_bot/
├── astra_bot/                 # основной Python-пакет
│   ├── adapters/              # OKX, другие биржи, WebSocket
│   ├── backtester/            # бэктестинг
│   ├── core/                  # состояние, конфиг, риск, readiness
│   ├── data/                  # данные
│   ├── decision/              # торговое решение
│   ├── engines/               # execution / risk / regime
│   ├── ml/                    # признаки, память, обучение, drift
│   ├── paperengine/           # виртуальное исполнение
│   ├── strategies/            # стратегии
│   ├── telegram/              # Telegram
│   └── main.py
├── scripts/                   # CLI и операционные сценарии
├── tests/                     # unit + integration
├── config/                    # конфигурация
├── docs/                      # документация
├── deploy/                    # VPS/systemd
├── monitoring/                # Prometheus/Grafana
├── models/                    # state и исследовательские артефакты
├── .github/workflows/         # CI/CD и scheduled jobs
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── requirements.txt
```

### Основные точки входа

| Задача | Файл |
|---|---|
| Исторический research | `scripts/pretrain_exhaustive_5y.py` |
| Защищённый Demo worker | `scripts/demo_trader_safe.py` |
| Проверка OKX private API | `scripts/test_okx.py` |
| Утренний отчёт | `scripts/morning_report.py` |
| Decision pipeline | `astra_bot/decision/pipeline.py` |
| Market understanding | `astra_bot/ml/market_understanding.py` |
| Pattern memory | `astra_bot/ml/market_memory.py` |
| ML training | `astra_bot/ml/model_trainer.py` |
| Temporal validation | `astra_bot/ml/temporal_trainer.py` |
| Risk engine | `astra_bot/engines/risk_engine.py` |
| Readiness gate | `astra_bot/core/readiness.py` |

## ⚙️ GitHub Actions

| Workflow | Назначение |
|---|---|
| `demo-trader.yml` | исторический research + защищённый Demo worker |
| `daily-train.yml` | ежедневное обучение/обновление |
| `morning-report.yml` | Telegram-отчёт в 09:00 MSK |
| `bot.yml` | периодический worker |
| `market-aware-smoke.yml` | быстрый smoke-test |

## 🔐 Безопасность

OKX и Telegram credentials хранятся только в GitHub Secrets/runtime environment. Они не должны попадать в исходники, README, артефакты или логи.

Рекомендуемые права OKX:

```text
Read       ✅
Trade      ❌ до отдельного ручного разрешения
Withdraw   ❌ НИКОГДА
```

Если секрет когда-либо оказался в Git, его следует считать скомпрометированным и заменить. Удаление файла из последнего коммита не удаляет его из истории.

## 🧪 Проверки

```bash
python scripts/test_okx.py
python -m pytest tests/unit
python -m pytest tests/integration
python scripts/preflight.py
```

Локальная установка:

```bash
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate       # Windows
pip install -r requirements.txt
```

## 📖 Документация

Начинать с `docs/INDEX.md`. Далее: `ARCHITECTURE.md` → `DECISION_PIPELINE.md` → `SELF_PLAY.md` → `PROFIT_AND_TRAINING.md` → `RISK_AND_GOALS.md` → `GITHUB_ACTIONS.md`.

## 🚧 Статус

**Реализовано:** OKX integration, Demo-контур, risk layer, walk-forward simulation, lesson/pattern memory, ML pipeline, Telegram reporting, GitHub Actions и временный GitHub-hosted runtime.

**В работе:** MAX_HISTORY, длительная Demo-валидация, calibration/drift, надёжный checkpoint/resume и перенос на VPS.

**Запрещено:** автоматическое включение реальной торговли.

## ⚠️ Дисклеймер

ASTRA — экспериментальная система количественного анализа и автоматизации торговли. Исторические результаты, backtest, Demo PnL, ML-метрики и readiness score не гарантируют будущую прибыльность и не исключают убытки.

## Лицензия

MIT
