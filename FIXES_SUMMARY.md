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
