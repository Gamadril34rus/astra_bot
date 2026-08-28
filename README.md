# ASTRA BOT

> **Адаптивная исследовательская и demo-трейдинговая система для криптовалютного рынка.**

ASTRA сейчас работает как **research-first система**: сначала изучает рынок, события и последствия изменений цены, затем формирует и проверяет гипотезы, и проводит многолетний аудит торговых стратегий. Все стратегии находятся в режиме fail-closed: без присвоения статуса `champion` виртуальные/демо ордера не исполняются. Реальные деньги и автоматические реальные ордера отключены.

## 🧠 Главный принцип

Количество сделок и количество `lessons` не являются мерой интеллекта ASTRA.

```text
MAX_HISTORY
    ↓
Наблюдаемое состояние рынка
    ↓
Свечи / бары / объём / структура / уровни / каналы
    ↓
Momentum / volatility / indicators / correlations
    ↓
Liquidity / derivatives / news / cross-asset context
    ↓
Событие или рыночный режим
    ↓
Что произошло ПОСЛЕ события?
    ↓
1h / 4h / 1d / 3d / 7d / более длинные горизонты
    ↓
Статистическая агрегация
    ↓
Гипотеза
    ↓
Walk-forward / out-of-sample validation
    ↓
Подтвержденное или отвергнутое знание
    ↓
Virtual self-play
    ↓
OKX Demo Trading
```

ASTRA должна учиться не только на вопросе «какая сделка выиграла», а на вопросе **«какие наблюдаемые условия связаны с последующим движением рынка и когда эта связь перестаёт работать»**.

## 🔬 Research Engine

Исторический исследователь фиксирует состояние рынка только из данных, доступных в момент `t`, а будущие свечи используются исключительно для оценки последствий.

Исследуются:

- свечи и последовательности свечей;
- HH/HL/LH/LL и структура рынка;
- тренды, переходы режимов и боковики;
- уровни, pivots, каналы, breakout и retest;
- Fibonacci-контекст;
- объём, OBV, VWAP и аномальные объёмные события;
- RSI, Stochastic, CCI, ATR, ADX-подобные признаки;
- EMA и Bollinger Bands;
- momentum и mean reversion;
- корреляции и lead/lag между активами;
- ликвидность и доступные данные order book;
- funding, open interest и другие производные данные, когда источник их предоставляет;
- новости и рыночные события (бесплатные источники без ключей: **GDELT DOC 2.0** — основной, **Free Crypto News API** — дополнительный с безопасным fallback; платный `NEWS_API_KEY` удалён);
- BTC/ETH/market-wide context;
- volatility regime и изменение режима рынка.

Для каждого наблюдения сохраняются forward return, максимальное движение вверх/вниз и изменение волатильности на нескольких горизонтах. Базовые состояния без специального события также исследуются как контрольная группа.

### Гипотезы

Найденная закономерность является только **candidate**, пока не прошла независимую проверку. Исторический результат не превращается автоматически в торговое правило.

```text
Discovery
  → Validation
  → Walk-forward
  → Out-of-sample
  → Costs / slippage stress
  → Stability / sensitivity
  → Candidate becomes usable knowledge
```

ASTRA также должна хранить отрицательные результаты: условия, которые выглядят убедительно, но не дают устойчивого преимущества.

## 🧪 Методологии профессиональных трейдеров

В research framework используются как источники проверяемых гипотез идеи из нескольких школ:

| Подход | Что исследуется |
|---|---|
| Livermore | trend, pivot, breakout, confirmation, volume |
| Soros | macro regime, reflexivity, information shock |
| Druckenmiller | dominant driver, regime, conviction, invalidation |
| Tudor Jones | macro + technical timing + risk control |
| Trend following | persistence, breakout continuation, volatility scaling |
| Quant / statistical | momentum, mean reversion, cross-sectional effects, anomalies |
| Market microstructure | order flow, liquidity, spread, lead/lag |

Это **не жёсткие правила**. ASTRA должна проверять, работают ли эти идеи именно на криптовалютном рынке и в каких режимах.

## 🌍 Историческое обучение

Целевой universe содержит около **35 ликвидных USDT-пар**. Перед торговым циклом список сверяется с актуальными SPOT-инструментами OKX.

Исторический learner использует максимально доступную историю конкретного инструмента в пределах заданного safety limit. Для нового токена история может быть меньше пяти лет, для старого больше. Несуществующие данные не выдумываются.

Research выполняется **до self-play**. Виртуальные сделки являются дополнительным источником опыта, а не единственным способом обучения.

## 📚 Память

### Research Memory

`models/research_observations.jsonl` хранит наблюдения рынка и последствия событий.

`models/research_hypotheses.json` хранит агрегированные кандидаты: выборку, среднюю/медианную реакцию, positive/negative rate, разброс, максимальные движения и статус проверки.

### Lesson Memory

Закрытые виртуальные и Demo-сделки сохраняются отдельно: инструмент, время, направление, вход/выход, режим, признаки, PnL, влияющий фактор, counterfactual и рекомендация.

### Pattern Memory

`models/market_memory.json` агрегирует повторяющиеся торговые паттерны и исследовательские сведения.

Ни один тип памяти не считается доказательством прибыльности без независимой проверки.

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

GitHub Actions используется как временная инфраструктура. Worker должен сохранять state/checkpoint, уроки и исследовательскую память.

## 🛡️ Readiness Gate

Гарантировать отсутствие убытков на реальном рынке невозможно. Поэтому готовность оценивается не одним PnL, а совокупностью статистических и эксплуатационных критериев.

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

Дополнительно требуется подтверждённое research coverage и out-of-sample стабильность. Реальный запуск остаётся отдельным ручным решением.

## 📲 Telegram

Основной отчёт отправляется **один раз в день в 09:00 по Москве**:

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
├── astra_bot/
│   ├── adapters/              # OKX / WebSocket / market data
│   ├── backtester/            # бэктестинг
│   ├── core/                  # state / config / risk / readiness
│   ├── data/                  # данные
│   ├── decision/              # decision pipeline
│   ├── engines/               # execution / risk / regime
│   ├── ml/                    # research / features / memory / training / drift
│   ├── paperengine/           # виртуальное исполнение
│   ├── strategies/            # стратегии
│   ├── telegram/              # Telegram
│   └── main.py
├── scripts/
├── tests/
├── config/
├── docs/
├── deploy/
├── monitoring/
├── models/
├── .github/workflows/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── requirements.txt
```

### Основные точки входа

| Задача | Файл |
|---|---|
| Multicurrency MTF audit | `scripts/audit_multicurrency.py` |
| Strategy lab & audit | `scripts/strategy_lab.py` |
| Research engine | `astra_bot/ml/market_research.py` |
| Проверка OKX private API | `scripts/test_okx.py` |
| Утренний отчёт | `scripts/morning_report.py` |
| Decision pipeline | `astra_bot/decision/pipeline.py` |
| Market understanding | `astra_bot/ml/market_understanding.py` |
| Pattern / research memory | `astra_bot/ml/market_memory.py` |
| ML training | `astra_bot/ml/model_trainer.py` |
| Temporal validation | `astra_bot/ml/temporal_trainer.py` |
| Risk engine | `astra_bot/engines/risk_engine.py` |
| Readiness gate | `astra_bot/core/readiness.py` |

## ⚙️ GitHub Actions

| Workflow | Назначение |
|---|---|
| `morning-report.yml` | Telegram в 09:00 MSK |
| `bot.yml` | периодический worker |
| `strategy-lab.yml` | еженедельная walk-forward валидация портфеля стратегий |
| `market-aware-smoke.yml` | быстрый smoke-test |

## 🔐 Безопасность

Credentials хранятся только в GitHub Secrets/runtime environment и не должны попадать в исходники, README, артефакты или логи.

Рекомендуемые права OKX:

```text
Read       ✅
Trade      ❌ до отдельного ручного разрешения
Withdraw   ❌ НИКОГДА
```

## 🧪 Проверки

```bash
python scripts/test_okx.py
python -m pytest tests/unit
python -m pytest tests/integration
python scripts/preflight.py
```

Проверка стратегии из «Простой книги торговли» (`Simple Trading Book_compressed.pdf`
в корне проекта) на истории: `python scripts/backtest_book_2y.py --years 2` —
бэктест book_breakout за 2 года на 1h/4h, итоги в `docs/book_backtest_2y/summary.md`.

Исследование бесплатных обучающих правил (Babypips, Investopedia, Turtles,
Connors RSI-2, Bollinger, Ichimoku и др.): `python scripts/research_free_strategies.py` —
прогон 16 правил на истории BTC/USDT, итоги в `docs/free_strategy_research.md`.
Лучшее правило (time-series momentum, 45 дней) встроено как стратегия
`ts_momentum` в decision-движок.

Портфель стратегий с walk-forward валидацией (IS/OOS/история 2021–2026):
`python scripts/strategy_lab.py` — протокол отбора и портфель «трендовой
книги» (TSM-45 L/S, vol-target, ADX-фильтр), итоги в `docs/strategy_portfolio.md`.

## 📖 Документация

Начинать с `docs/INDEX.md`. Далее: `ARCHITECTURE.md` → `DECISION_PIPELINE.md` → `SELF_PLAY.md` → `PROFIT_AND_TRAINING.md` → `RISK_AND_GOALS.md` → `GITHUB_ACTIONS.md`.

Дополнительно: `docs/LEARNING_OBJECTIVES.md` и `docs/EDGE_RESEARCH_PLAN.md`.

## 🚧 Статус

**Реализовано:** OKX integration, защищённый Demo-контур, risk layer (в живом
paper-контуре: лимиты потерь, drawdown HALT, восстановление между CI-сессиями,
реальные fees/slippage в paper-счёте), **Meta-Strategy — выбор стратегии по
EV в текущем рыночном режиме с bayesian shrinkage по sample size** (не по
`total_score`), **NO_TRADE-наблюдения с future-outcome по горизонтам 1/3/6/12/24
бара** (отказ от сделки — тоже обучение), walk-forward simulation,
research-first memory, lesson/pattern memory, ML pipeline, Telegram reporting
и GitHub Actions.

**В работе:** длительная Demo-валидация, calibration/drift, надёжный checkpoint/resume и перенос на VPS.

**Запрещено:** автоматическое включение реальной торговли.

## ⚠️ Дисклеймер

ASTRA — экспериментальная система количественного анализа и автоматизации торговли. Исторические результаты, backtest, Demo PnL, ML-метрики и readiness score не гарантируют будущую прибыльность и не исключают убытки.

## Лицензия

MIT
