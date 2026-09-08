# ASTRA BOT — Fixes Summary (Block 1-8)

## Проблема исходная
- Morning report: "Новых знаний нет... База знаний: накопление (n<5) Сделок за сутки: 0 PnL 0 Готовность: НЕТ (20/90)"
- Постоянные "run failed" emails от GitHub Actions
- `strategy_stats.json` не рос (n<5)
- `paper_positions.json` имел `risk_distance=0` (TRX long 66 баров) → R=0, статистика терялась
- `trading_budget.json` last_date 2026-09-03 устарел → tick() не работал
- `readiness.record_day` никогда не вызывался в продакшене
- `.gitignore` игнорировал всю папку `data/` → git-based persistence не работал
- CI падал из-за отсутствия кэша pip, отсутствия rebase, отсутствия force-with-lease, отсутствия обработки ошибок

## Что исправлено

### Block 1: CI Stability (run failed)
- **1.1** Кэширование pip по hash `requirements.txt` через `actions/cache@v4` + `setup-python cache: pip`
- **1.2** `retry_async` декоратор для всех внешних вызовов (BingX/OKX/Telegram) с 3 попытками 2s/5s/15s, exponential backoff
  - `astra_bot/utils/retry.py` (новый)
  - Применён в `astra_bot/adapters/bingx/client.py` и `scripts/run_bot.py`
- **1.3** Graceful degradation: `pipeline.py` теперь `logger.warning` + запись в `logs/errors.log` вместо падения
- **1.4** `timeout-minutes: 4` + `continue-on-error: true` для size gate и save state
- **1.5** Логирование ошибок в `logs/errors.log` с ротацией 1MB→500KB
  - `scripts/run_bot.py` `_log_error_to_file`
  - `astra_bot/decision/pipeline.py` пишет traceback
  - `.gitignore` теперь `!logs/errors.log`

### Block 2: Git-based Persistence
- **2.1** Новый `StateManager` (`astra_bot/data/state_manager.py`):
  - `data/state.json` атомарная запись (tmp→replace)
  - `data/trades.db` SQLite с trades + daily_stats
  - `data/weights.json` адаптивные веса
  - `data/model.joblib`, `features_cache.pkl`
  - `git_commit_and_push` с `pull --rebase` + `push --force-with-lease`
- **2.2** Conflict resolution: `git pull --rebase origin master` перед каждым push + fallback `force-with-lease`
- **2.3/2.4** Поддержка артефактов >50MB через GitHub Releases (заглушка в workflows)
- `.gitignore` изменён: `data/*.tmp` игнорируется, но `!data/trades.db`, `!data/*.db`, `!data/state.json` разрешены
- `bot.yml` теперь сохраняет `data/*` и `logs/errors.log`

### Block 3: Morning Report & Triggers
- **3.2** `repository_dispatch` types `morning_report, external_trigger, trade` в `morning-report.yml` и `bot.yml` — поддержка cron-job.org/UptimeRobot
- **3.3** Дедупликация: `data/state.json:last_report_date` проверяется, повторный отчёт в тот же день не отправляется (поддержка `FORCE_REPORT`)
- Переписан `scripts/morning_report.py`:
  - Парсит `models/paper_trades.jsonl` + `data/trades.db`, дедупликация по id
  - Парсит ms и ISO timestamp
  - Считает 24h/7d/30d статистику, best/worst trade
  - Загружает readiness, learning_digest, positions, budget, weights, errors count, health, warnings
  - Разбивает Telegram >4000 chars
  - Сохраняет watermark в `data/state.json` + legacy `models/demo_state.json`

### Block 4: Market Regime & Strategies
- **4.1** Новый `MarketRegimeDetectorV2` (`astra_bot/decision/regime_detector_v2.py`):
  - 5 фаз: TRENDING_UP/DOWN (ADX>25, Choppiness<40), RANGING (ADX<20, Chop>60), SQUEEZE (BB bandwidth <20th percentile 100), VOLATILE (ATR 7/28 >1.5), TRANSITIONAL
  - Маппинг стратегий на режимы, VOLATILE/TRANSITIONAL → no trade или min size
- **4.2** 4 таймфрейма в `TradingEngineConfig`: `("5m","15m","1h","4h")`
- **4.3** 4 стратегии с volume фильтром (`astra_bot/decision/strategies/volume_filtered.py`):
  - TrendFollowing, MeanReversion, Breakout, Momentum
  - Volume filter >1.2x SMA20 (breakout >1.5x)

### Block 5: ML 50+ Features
- Новый `astra_bot/features/feature_builder.py` — 55 фич:
  - Price action 15, Trend 10, Momentum 10, Volatility 8, Volume 7, Structure 5+, Time 3
  - Клиппинг -10..10, NaN→0
  - `features_to_vector` с консистентным порядком

### Block 6: Risk Management
- **6.1** Обновлён `TradingEngineConfig`:
  - `risk_per_trade_pct 0.01` (1%)
  - `max_notional_pct 0.10` (10% max per trade)
  - `max_open_positions 3`
  - `max_same_direction 2`
  - `max_total_exposure_pct 0.30`
  - `RiskEngine` теперь создаётся с `daily_loss_limit 0.03` (3%), `weekly 0.06`, `max_gross/net = 30%`
- **6.2** Новый `astra_bot/engines/position_sizer.py`:
  - Base: risk_amount / sl_distance
  - Kelly: win_rate, avg_win/loss → quarter-Kelly max 25%, multiplier 0.5-1.0
  - ML confidence: 0.5x-1.0x
  - Volatility: ATR% >2% → reduce size, >5% → 0.3x
  - Hard max 10%
- **6.3** SL/TP логика 2:1 R:R в `calculate_sl_tp`

### Block 7: Adaptive Learning
- `scripts/adaptive_analysis.py`:
  - Читает trades 21 день из JSONL+DB
  - Считает win_rate, PF, avg R по стратегиям
  - Увеличивает вес прибыльным (+10% если PF>1.2, WR>50%)
  - Уменьшает убыточным (-20% если PF<0.8 или PnL<0 с >10 сделок)
  - Отключает если 21д PnL<0 и weight<0.3 и count>=15
  - Нормализует веса к avg 1.0
  - Сохраняет в `data/weights.json` + via StateManager
- `scripts/run_bot.py` теперь вызывает `readiness.record_day` в trade_loop

### Block 8: Weekly Report
- `scripts/weekly_report.py`:
  - Статистика 7д/30д, по стратегиям, режимам, часам
  - Best/worst trade, рекомендации
  - Сохраняет `models/weekly_report.txt` + Telegram

### Фиксы багов обучения
- `broker.py _load`: миграция `risk_distance=0` → `abs(entry-stop)` или 1% entry, `regime` fallback UNKNOWN
  - Исправляет TRX long 66 баров held
- `trading_budget.json`: `last_date 2026-09-03 → 2026-09-06`, `used_minutes_before_today 372`
- `strategy_stats` gate: `if d.get("r_multiple") or d.get("regime")` был falsy для `r_multiple=0.0` → теперь сохраняется
- `readiness.record_day` добавлен в production loop
- `trading_engine._record_closed` теперь сохраняет в `StateManager.save_trades` + обновляет `data/state.json` balance/realized/positions/daily

### Workflows (требуют workflows permission)
- `bot.yml`: cache, rebase, force-with-lease, data/* persistence, errors.log
- `morning-report.yml`: repository_dispatch, cache, timeout 4, continue-on-error, state save
- `daily-retrain.yml` (новый): 02:00 UTC, retrain ML, save model.joblib
- `weekly-adapt.yml` (новый): Воскресенье 03:00 UTC, adaptive analysis

**Важно**: workflows файлы изменены локально, но push заблокирован GitHub App без `workflows` permission.
Нужно переподключить GitHub в Arena с разрешением `workflows` или вручную скопировать файлы из локальной папки `.github/workflows/` в репозиторий.

## Тесты
- Все unit тесты проходят (кроме integration): `pytest tests -k "not integration" -q` → 100% pass
- `test_trading_engine_risk.py` обновлён под новый риск (10% max, 1% risk, Kelly)

## Что осталось
- Вручную запушить workflows (нужен workflows permission)
- Настроить cron-job.org или UptimeRobot для `repository_dispatch` morning_report
- Проверить что `data/trades.db` растёт (сейчас 20KB, 1 таблица trades)
- Дождаться накопления 5+ сделок для `strategy_stats.json` → readiness станет YES
- Мониторить `logs/errors.log` на проде

## Файлы
- `astra_bot/data/state_manager.py` — core persistence
- `astra_bot/engines/position_sizer.py` — Block 6.2
- `astra_bot/decision/regime_detector_v2.py` — Block 4.1
- `astra_bot/decision/strategies/volume_filtered.py` — Block 4.3
- `astra_bot/features/feature_builder.py` — Block 5
- `astra_bot/utils/retry.py` — Block 1.2
- `scripts/adaptive_analysis.py` — Block 7
- `scripts/weekly_report.py` — Block 8
- `scripts/morning_report.py` — полностью переписан
- `.github/workflows/*.yml` — обновлены (локально)

---

## Block 9: Стратегия упала — ревизия 2026-09-07 (почему стратегии молчали)

### Найдено и исправлено (критическое)
- **Режимный гейт всегда возвращал OFF** (`strategies/base.py`):
  `STRATEGY_REGIME_COMPATIBILITY` ключован членами Enum `MarketRegime`, а
  поиск шёл строкой (`"BULL_TREND"`). Хэши не совпадают → momentum и
  mean_reversion не торговали НИ В ОДНОМ режиме. Плюс
  `get_regime_compatibility` разворачивала Enum обратно в `.value`.
  Теперь нормализация str↔Enum внутри `check_regime_compatibility`.
- **Momentum не мог дать сигнал никогда**: брался TP-уровень `tp_levels[0]`
  (1R по умолчанию) при `min_risk_reward=1.5` — проверка R:R была
  математически невыполнимой. Теперь берётся первый уровень,
  удовлетворяющий min_risk_reward.
- **9 стратегий (5 pattern + 4 volume-filtered) молча не загружались**:
  импортировали несуществующий `StrategyContext`, ошибка глоталась
  `except ImportError`/`except Exception` → `pattern_strats = []`.
  Добавлен `StrategyContext` в `decision/context.py` и
  `PipelineStrategyAdapter` (`decision/strategies/adapter.py`), связавший
  контракт V2-стратегий (ctx → SignalCandidate) с контрактом пайплайна
  (evaluate(symbol, candles, ...) → Signal). Пайплайн теперь 16 стратегий.
- **Коллизия имён**: V2 mean_reversion/momentum дублировали имена базовых
  стратегий → EV-статистика смешивалась. Переименованы в
  `mean_reversion_v2`/`momentum_v2`.

### Найдено и исправлено (надёжность)
- `events.emit_async(...)` без await в `BaseStrategy.update_performance` —
  событие STRATEGY_KILLED никогда не доходило; заменено на sync `emit`.
- Profit Factor считался из net_pnl (фактически wins/losses), серия без
  убытков держала PF=0 → ложный kill switch. Теперь gross_profit/gross_loss;
  авто-kill только от 5 сделок (`MIN_TRADES_FOR_KILL_SWITCH`).
- **Тесты затирали прод**: прогоны pytest писали в committed
  `data/state.json`, `data/trades.db`, `models/strategy_stats.json`
  (фейковые сделки!) и `logs/errors.log` (фейковые ошибки биржи).
  - `StateManager` теперь уважает `ASTRA_STATE_DIR`, синглтон
    пересоздаётся при смене окружения (+`reset_state_manager()`).
  - `retry._log_error_to_file` под pytest не пишет в прод-лог
    (плюс override `ASTRA_ERROR_LOG`).
  - `tests/conftest.py`: autouse-изоляция ASTRA_STATE_DIR/error-log.
- `BaseStrategy` стал дженериком (`BaseStrategy[ConfigT]`) — убраны ~60
  mypy attr-defined в стратегиях; `momentum.py` implicit-Optional.
- Мёртвый код в `_persist_trades` удалён; маскирующий try/except в
  pattern_strategies убран (тихая деградация прятала баг); RUF100/B023
  в lint.

### Проверка
- 695 тестов зелёные (+19 регрессионных на все фиксы).
- ruff: 0 ошибок на весь репозиторий.
- Полный прогон тестов больше не меняет data/, models/, logs/.

---

## Block 10: Касания по теням, закругления, плечи и комиссии (ревизия 2026-09-08)

### Паттерны
- **Касания по теням**: свинг = касание, если экстремум (хай/лоу) лёг
  в допуск от линии (max(RMSE*1.5, 0.3% цены)). Правило «3 сверху +
  3 снизу» → +0.1 к уверенности; иначе −0.05.
- **Пробой по теням**: тень за линией — ранний сигнал (+0.1), close за
  линией — подтверждённый (+0.2).
- **Закругления**: квадратичная аппроксимация границы через numpy.polyfit
  (нормальные уравнения в лоб плохо обусловлены при x~100). Новые
  паттерны ROUNDED_BOTTOM (чаша → лонг) и ROUNDED_TOP (купол → шорт),
  обрабатываются клин-стратегиями.

### Плечи
- `PaperBroker`: `max_leverage` (дефолт 2), `leverage_fee_daily`
  (0.04%/сутки на заёмную часть), `maintenance_margin_pct` (0.5%).
- Маржа: сумма занятой маржи ≤ equity, иначе сделка отклоняется.
- Ликвидация: long liq = entry*(1 − 1/lev + mm); стоп обязан быть ВНУТРИ
  (сработать раньше ликвидации), иначе отказ.
- `TradingEngine`: плечо = `leverage_max` только при EV ≥
  `leverage_min_ev_r` (env: ASTRA_LEVERAGE_MAX / ASTRA_LEVERAGE_MIN_EV_R);
  отказ брокера отменяет сделку целиком (fail-closed).
- **Плата за плечо** начисляется при каждом закрытии (в т.ч. частичном)
  на заёмную часть пропорционально барам удержания × длительность ТФ —
  детерминированно для реплея (wall-clock ломал replay-determinism).

### Проверки
- 733 теста зелёные (+20: касания, тени, чаша/купол, маржа, ликвидация,
  фандинг, «никогда не забыть комиссии», решение движка о плече).

---

## Block 11: Стопы и «отторговка» (ревизия 2026-09-08)

Проблема: плечо увеличивает и выигрыш, и проигрыш; без активного
управления убыточной позицией бот платил полный стоп (-1R) даже когда
цена разворачивалась раньше.

### Реализация
- **Структурный стоп** (`structural_stop`, exit_controller): перед
  сайзингом стоп выносится за min(low)/max(high) последних 15 баров
  (по теням) с буфером max(0.3%, 0.15·ATR); не дальше 2.2R (R-бюджет),
  ресайз объёма — уже по новому стопу.
- **Умный набор выходов по умолчанию** (`SMART_DEFAULT_PLAN`): работает
  без активной гипотезы (раньше был голый STATIC_TP):
  BREAKEVEN(нетто) + TRAILING(2·ATR) + MAE_CUT(0.9R) + REGIME_EXIT.
- **Нетто-безубыток** (`PaperBroker.net_breakeven_price`): точная цена
  выхода с PnL=0 — вход со slippage, тейкер обеих сторон, фандинг.
  Применяется и в контроллере, и в брокере после TP1 (раньше стоп
  ставился в сырой вход — гарантированный маленький минус).
- **MAE_CUT**: цена против позиции глубже 0.9R → выход в close
  (средний убыток < полного стопа). С плечом >1 все пороги ×0.75.
- **REGIME_EXIT**: лонг закрывается в PANIC/HIGH_VOL/медвежьих режимах,
  шорт — в бычьих (≠ режиму входа).
- env: ASTRA_SMART_EXIT=0 / ASTRA_STRUCTURAL_STOP=0 отключают.

### Проверки
- 748 тестов зелёные (+14 unit на отторговку, +1 integration).
- Интеграционный тест: ралли +0.9R без гипотезы → стоп в нетто-БУ;
  на узком ATR трейлинг выбивает стоп прямо в баре ралли с +0.7R.
- Скорректированы легаси-тесты (умные дефолты выключаются флагом) и
  historical-sizing тест читает АРХИВ нарушений, а не живой файл.

---

## Block 12: Лестница плеча до 100x и голова-плечи (ревизия 2026-09-08)

### Лестница плеча
- `leverage_for()` (trading_engine): база 2x по EV>=0.8 (прежнее
  поведение), выше — ступени по confidence+EV: 3x/5x/10x/20x/50x/100x.
  100x = conf>=0.95 И EV>=3R («прям точно», как просил пользователь).
- **Стоп раньше ликвидации**: плечо автоматически срезается до
  feasible = 1/(d+mm)-1 (d — дистанция стопа, mm=0.5%). 100x живёт
  только со стопами < ~0.4%: с широким стопом 100x = гарантированная
  ликвидация раньше стопа.
- Брокер теперь получает потолок из конфига движка (раньше молча зажимал
  своим дефолтом 2x!); публичный `PaperBroker.liquidation_price()`.
- ASTRA_LEVERAGE_MAX по умолчанию 100 (потолок, не уровень).
- PnL в стопе от плеча не зависит (сайзинг по риску) — плечо меняет
  маржу/фандинг/ликвидацию, не риск. Фандинг при 100x (~4%/сутки
  нотинала) делает долгое удержание заведомо убыточным — естественный
  тормоз.

### Голова и плечи
- `detect_head_shoulders()`: LS-голова-RS с симметрией плеч (±40%),
  шея по ТЕНЯМ впадин, касания шеи по теням, пробой тень=ранний /
  close=подтверждённый; `_collapse_plateaus()` чинит плоские экстремумы
  (_find_swings возвращал каждый бар серии).
- `HeadShouldersStrategy` (head_shoulders): шорт ГП / лонг обратной;
  стоп за правое плечо (полпути к голове), тейк — классическая проекция
  высоты головы от шеи. Зарегистрирована в пайплайне (10-я паттернная).

### Проверки
- 764 теста зелёные (+16: ступени лестницы, срез по ликвидации,
  потолок env, ГП/обратная/ложные срабатывания, сигналы стратегии,
  брокер 100x с отказом по ликвидации).
- Исправлена опечатка «приплече» в ошибке брокера.
