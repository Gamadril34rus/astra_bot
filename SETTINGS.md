# SETTINGS — канон live/paper Zeus

Дата среза: **05.11.2026**. Этот файл описывает именно Zeus. Не использовать его как замену общему конфигу проекта и наоборот. См. [карту конфигурации](docs/CONFIGURATION_MAP.md).

**Не менять риск, плечо, exit-engine, флаги окружения и стоп-краны без прямого слова владельца.** Источник чисел — отмеченный код или workflow. Столбец «Live workflow» показывает значение в `.github/workflows/zeus-live-clock.yml`, а не обещает, что секреты владельца имеют определённые значения.

## Канонические параметры Zeus

| Параметр | Источник | Default в коде | Фактически в live workflow |
|---|---|---:|---:|
| Confidence risk ladder | `live_arm.py`, `conf_risk_ladder.py` | confidence ≥0.88 → 3%; ≥0.80 → 2%; ≥0.70 → 1.5%; ниже — fallback 1% | Та же лестница; применяется только при разрешённом arm-входе |
| MAX_OPEN | `live_arm.py` | 5 | 5 (кодовое ограничение) |
| SAME_SIDE_SOFT_CAP | `live_arm.py` | 3; дополнительный hard cluster cap = 4 | 3 / 4 |
| INJ_SLOT_RESERVE | `live_arm.py` | 2 свободных слота для major | 2 |
| Cooldown входа | `live_arm.py` | 20 минут на strategy / symbol / side | 20 минут |
| FRESH_MAX_SEC | `live_arm_wiring.py` | 2700 секунд (45 минут) | 2700 секунд |
| Дневной / недельный убыток | `live_risk.py` | 4% / 6% от реального equity | 4% / 6% |
| Kill PF threshold | `live_risk.py` | HALT при n ≥ 5 и PF < 1 | n ≥ 5 и PF < 1 |
| ZEUS_KILL_PF_WINDOW | `live_risk.py` + live workflow | 0 / пусто = cumulative | 20 последних подходящих exits после reset/epoch |
| MAX_LEVERAGE | `LiveBrokerConfig` | 10 | `ZEUS_LIVE_MAX_LEVERAGE=10` |
| Default leverage | `LiveBrokerConfig`, candidate | 5 до расчёта / override | Входы принудительно используют `ZEUS_LIVE_FORCE_LEVERAGE=6` |
| Leverage ladder | `live_arm.py` | confidence/EV ступени 10/5/3 с ограничением max | Доступный верхний лимит 10; force leverage 6 |
| Take RR clamp | `exit_plan.py` | [2.0; 2.3]R | [2.0; 2.3]R |
| TP quantity fractions | `exit_plan.py` `PLAN_TP_FRACTIONS` | 30% / 70% | 30% / 70% |
| Entry cooldown после VOL_EXPANSION | `reentry_guard.py` | 1800 секунд (30 минут) | 30 минут |
| Exit-engine | `live_exit_engine.py` | `shadow` | `ZEUS_LIVE_EXIT_ENGINE=on` |
| Trail policy | `live_exit_engine.py` | `atr` | `ZEUS_LIVE_TRAIL_POLICY=atr` |
| ATR trailing | `live_exit_engine.py`, `exit_plan.py` | 2 × ATR(14), 4h с fallback на 1h | 2 × ATR(14) |
| MAE_CUT | `live_exit_engine.py`, `exit_plan.py` | 0.9R; leverage trigger scale 0.75 | 0.9R × scale 0.75 |
| MAX_HOLD | `live_exit_engine.py` | 48 часов | 48 часов |
| VOL_EXPANSION | `exit_plan.py` / exit manager | ratio 2.0 | 2.0 |
| D1 structural tighten | `exit_plan.py` | buffer_pct 0.003; только подтягивание | 0.003; только подтягивание |
| Zone shadow | `zone_shadow.py` | `ZONE_SHADOW=0` (выключен) | `ZONE_SHADOW=1` |
| Session profile | `live_session.py` | `ZEUS_SESSION_PROFILE=0` (выключен) | `ZEUS_SESSION_PROFILE=1` |
| Session BE / TP1 (static) | `live_session.py` | BE 0.5R / TP1 0.6R, если профиль выключен | В workflow заданы 0.5 / 0.6, но при профиле = 1 профиль сессии их переопределяет |
| ZEUS_PF_SKIP_SCRATCHED | `live_risk.py` | 1 (игнорировать scratched exits) | 1 |
| ZEUS_PF_INCLUDE_PARTIAL | `live_risk.py` | 1 (учитывать partial_fill с pnl) | 1 |
| ZEUS_STOP_PULL_PCT | `live_exit_engine.py` | 0 (выключен) | 0 |
| ZEUS_RETEST_V1 | `zeus-live-clock.yml` | Shadow-режим задаётся кодовым/runner default | `shadow` |
| ZEUS_MIN_STOP_PCT | `live_arm_wiring.py` | 0.01 | 0.01 |
| ZEUS_DEAD_FLAT_EXIT | `zeus_live_clock.py` | 1 (включён); age=12h, MFE=0.15R | 1; age=12h, MFE=0.15R |

### Как читать сессионные значения

В live workflow включено `ZEUS_SESSION_PROFILE=1`. Поэтому BE/TP1 выбираются по часу МСК и дню недели. Значения `ZEUS_BE_TRIGGER_R=0.5` и `ZEUS_TP1_RR=0.6` из workflow используются как static значения при выключенном профиле; при включённом профиле они **не являются override**, если `ZEUS_SESSION_LOCK_ENV=1` не задан.

| Профиль | Будни, МСК | Выходные, МСК | BE trigger | TP1 RR | Новые входы |
|---|---|---|---:|---:|---|
| fat | 10–12, 16–20 | не применяется | 0.50R | 0.70 | да |
| normal | 03–06, 13–15, 21–22 | 16–19 | 0.40R | 0.55 | да |
| thin | 00–02, 23 | 10–12, 15, 20–21 | 0.30R | 0.45 | да |
| off | 07–09 | остальные часы | 0.30R | 0.45 | нет; открытые позиции сопровождаются |

Источники: `astra_bot/decision/live_session.py`, `ZEUS_SESSION_PROFILE=1` в live workflow.

## Live workflow и secrets

Фактические значения из `.github/workflows/zeus-live-clock.yml`:

| Имя | Откуда поступает | В workflow |
|---|---|---|
| `ENVIRONMENT` | YAML | `live` |
| `ZEUS_LIVE` | GitHub Actions Secret | Проверяется guard; без значения `1` live-адаптер не запускается |
| `ZEUS_VST`, `ZEUS_MAINNET` | GitHub Actions Secrets | Выбор окружения по guard в коде; значения секретов не видны в YAML |
| `ZEUS_ARM` | GitHub Actions Secret | Разрешение новых arm-входов |
| `BINGX_API_KEY`, `BINGX_API_SECRET` | GitHub Actions Secrets | Передаются в процесс; не печатать и не сохранять в git |
| `TELEGRAM_LIVE_BOT_TOKEN`, `TELEGRAM_ADMIN_ID`, `TELEGRAM_CHAT_ID` | GitHub Actions Secrets | Telegram-команды и уведомления |
| `TG_TEST` | GitHub Actions Secret | Передаётся live clock |
| `ZEUS_RETEST_V1`, `ZONE_SHADOW` | YAML | `shadow`, `1` |
| `ZEUS_LIVE_FORCE_LEVERAGE`, `ZEUS_LIVE_MAX_LEVERAGE` | YAML | `6`, `10` |
| `ZEUS_KILL_PF_WINDOW` | YAML | `20` |
| `ZEUS_LIVE_EXIT_ENGINE`, `ZEUS_LIVE_TRAIL_POLICY` | YAML | `on`, `atr` |
| `ZEUS_BE_TRIGGER_R`, `ZEUS_SESSION_PROFILE`, `ZEUS_TP1_RR` | YAML | `0.5`, `1`, `0.6` (BE/TP1 подчиняются профилю сессии, см. выше) |
| `ZEUS_STOP_PULL_PCT` | YAML | `0` |
| `ZEUS_PF_SKIP_SCRATCHED`, `ZEUS_PF_INCLUDE_PARTIAL` | YAML | `1`, `1` |
| `ZEUS_MIN_STOP_PCT` | YAML | `0.01` |
| `ZEUS_DEAD_FLAT_EXIT`, `ZEUS_DEAD_FLAT_AGE_H`, `ZEUS_DEAD_FLAT_MFE` | YAML | `1`, `12`, `0.15` |

Секреты владельца не раскрываются этим документом. Значения Secret нельзя выводить из имени переменной или из пустого/непустого поля YAML.

## Общая конфигурация проекта — НЕ Zeus

`config/settings.yaml` — общий конфиг ASTRA BOT. В нём есть `risk_per_trade=0.004` (0.4%), `daily_loss_limit=0.02` (2%) и `weekly_loss_limit=0.04` (4%). **Эти значения не заменяют Zeus-канон 4% дневного / 6% недельного ограничения, Zeus risk ladder и Zeus-параметры.** Не копировать значения в обе стороны. Бумажный Zeus отдельно описан в `config/paper_zeus_clock.yaml`.

## Операционные запреты

- ON/режим exit-engine, изменения риска, ENV, Disable/Enable workflows и любые действия с real — только прямое слово владельца.
- Не править `models/` вручную ради документации.
- Force-push и отключение Gitleaks запрещены.
