# Hypothesis Engine

Статус: реализовано, подключено к live paper-конттуру (2026-08-28).
Реализует TZ §9–§11, §15 (ссылка на гипотезу в lessons), §31 (деградация).

## Lifecycle

```text
DISCOVERED → TESTING → VALIDATED → ACTIVE → WEAKENING → INVALIDATED → RETIRED
                ↘ INVALIDATED          ↖ (восстановление)
```

| Переход | Требование |
|---|---|
| DISCOVERED → TESTING | данные собраны |
| TESTING → VALIDATED | **sample_size ≥ min_samples (20)** + метрики по **всем** периодам (train, validation, OOS, walk-forward) с expectancy > 0 + **stress test** (TZ §11/§22) |
| TESTING → INVALIDATED / DISCOVERED | проверка не подтвердила / мало данных |
| VALIDATED → ACTIVE | допущена к live-учту |
| ACTIVE → WEAKENING | live-статистика деградировала (автомат, см. ниже) |
| WEAKENING → ACTIVE | live-восстановление |
| WEAKENING → INVALIDATED | исчезновение преимущества доказано |
| INVALIDATED → RETIRED | окончательный вывод; **запись не удаляется** (история — часть памяти, TZ §10) |

Некорректные переходы отклоняются; `INVALIDATED` требует `invalidation_reason`.

## Запись (TZ §9.1)

`id, created_at, updated_at, description, strategy_id, features, conditions,
symbols, timeframes, market_regimes, sample_size, train/validation/oos/
walk_forward metrics, stress_metrics, expectancy, profit_factor, win_rate,
mfe, mae, confidence, status, parent_hypothesis, version,
invalidation_reason, status_log[]`.

Строгое правило TZ §11 — **VALIDATED нельзя получить на нескольких
прибыльных сделках**: тест `test_three_winning_trades_not_enough`
(3 сделки × 1.0R → отклонено, sample_size < min).

## Live-мониторинг (TZ §31)

`TradingEngine._record_closed` → после записи в `StrategyStatsStore`:
если у стратегии есть ACTIVE-гипотеза и live-выборка ≥ 20 сделок —
`HypothesisStore.check_live_degradation()`:

```text
degraded ⇔ live_expectancy < 0.5 × validated_expectancy
            или live_expectancy < 0 (при validated > 0)
```

Результат: ACTIVE → WEAKENING + строка
`HYPOTHESIS hyp-... DEGRADED: ACTIVE -> WEAKENING (live expectancy ...)`.
Восстановление (WEAKENING → ACTIVE) — ручным/циклическим вызовом
`store.transition()` с reason.

## Persistence

`models/research/hypotheses.json` (атомарно, tmp+rename; в CI save-state).
Перезапуск CI-сессии не теряет lifecycle.

## Миграция legacy

`scripts/init_hypotheses.py --root models --min-samples 20` — переносит
агрегаты из `models/research_hypotheses*.json` (market_research, единственный
статус `candidate`, два формата узлов: плоский и вложенный
discovery/validation) в DISCOVERED-гипотезы со стабильным id
(content-hash ключа) — идемпотентно. Сделано: **442 сканировано,
175 мигрировано, 267 пропущено** (sample < 20). Метрики: discovery →
train_metrics, validation (если samples > 0) → validation_metrics.
OOS/walk-forward/stress у legacy-агрегатов отсутствуют — переход в
VALIDATED для них заблокирован до сбора доказательств (корректное
поведение, а не пробел).

## Тесты

`tests/unit/test_hypothesis_engine.py` — happy path, запрещённые переходы,
терминальный RETIRED, обязательный reason, invalidation reason,
weakening/recovery, требования VALIDATED (sample, каждый период, stress,
отрицательный OOS), persistence с историей, dedup, active_for,
нет-удаление после retire, live-деградация (5 сценариев).
