# Exit Research

Статус: реализовано, подключено к live paper-конттуру (2026-08-28).
Реализует TZ §16/§17.

## Варианты выхода (8)

| Вариант | Параметр | Механика |
|---|---|---|
| `STATIC_TP` | — | фиксированный тейк (дефолт, текущее поведение) |
| `ATR_STOP` | `k=2.0` | стоп = k × ATR14 (применяется на первом баре позиции) |
| `STRUCTURE_STOP` | `lookback=10` | стоп на swing low/high последних баров |
| `TRAILING` | `k=2.0` | стоп = экстремум в пользу ± k × ATR |
| `BREAKEVEN` | `trigger_r=1.0` | стоп в точку входа после trigger_r × R (по MFE) |
| `TIME_STOP` | `bars=12` | вынужденное закрытие через n баров (в close) |
| `MOMENTUM_EXIT` | `ema=9` | вынужденное закрытие при пересечении цены EMA |
| `REGIME_EXIT` | `exit_regimes` | вынужденное закрытие при неблагоприятной смене режима |

## Два слоя

1. **Исследование** (`astra_bot/ml/exit_research.py`) — чистые оценщики:
   - `evaluate_exit(bars, entries, variant, params, fee_pct, slippage_pct)` —
     метрики варианта на выборке входов (R net, MFE/MAE, win rate, PF);
   - `walk_forward_evaluate(..., folds=3)` — временная разбивка на
     **нескользящие блоки: train / validation / OOS** + walk-forward как
     средняя OOS-оценки; вход учитывается только из своего блока, выход —
     строго на более поздних барах, ATR/EMA/структура — только по данным до
     текущего бара (нет lookahead, TZ §21);
   - `register_exit_hypothesis(store, ...)` — результат становится
     **гипотезой** (общая память, TZ §14) и проходит тот же lifecycle, что и
     торговые гипотезы: VALIDATED только при train+validation+OOS+
     walk-forward (все с expectancy > 0) + stress + sample size (TZ §11).
     «Идеальный выход, подогнанный под тест» (TZ §17) ACTIVE не становится.

2. **Применение** (`astra_bot/decision/exit_controller.py`) — на каждом
   новом баре в `process_symbol`:
   ```text
   broker.update_extremes(bar)
   decision = pipeline.decide(ctx)
   exit_controller.apply(broker, symbol, bar, bars, regime)   # план выхода
   broker.check_exits(bar)
   ```
   - план выбирается из **ACTIVE**-гипотез стратегии/режима
     (`plan_for`), иначе `STATIC_TP` — live-поведение не меняется
     без доказательств (никогда не автозаменяет работающую модель);
   - корректирует `pos.stop_loss` (ATR/STRUCTURE/TRAILING/BREAKEVEN)
     и/или принудительно закрывает (TIME/MOMENTUM/REGIME) — закрытия
     идут тем же путём, что и стоп/тейк (fees/slippage, R-метрики,
     lessons, stats, RiskEngine).

## Брокер

`PaperPosition.bars_held` (счётчик баров), `PaperBroker.close_position(id)`,
разделение `on_bar` на `update_extremes` + `check_exits` (совместимый
`on_bar` сохранён). R-единица для exit-правил — `risk_distance`
(исходный риск), не текущий подтянутый стоп.

## Live-демонстрация (integration-тест)

Скальп-сигнал → LONG. С ACTIVE-гипотезой `BREAKEVEN (trigger_r=0.8)`:
рост на 0.9R (ниже TP) → стоп в точку входа → откат → выход в **0R**.
Без гипотезы тот же сценарий → выход в **−1R** (поведение не изменилось).
`TIME_STOP (bars=3)` → принудительное закрытие на 3-м баре.

## Тесты

- `tests/unit/test_exit_research.py` — все 8 вариантов на детерминированных
  путях (точное R-значение), costs×2 стороны, walk-forward-разбиение без
  пересечения, gating продвижения (отрицательный OOS / малая выборка /
  storage stress), стабильность id.
- `tests/integration/test_exit_controller_execution.py` — реальный
  production path: BREAKEVEN защищает сделку, дефолт без гипотезы
  не меняется, TIME_STOP закрывает.

## Ограничения

- Оценщик исследует варианты на **исторических входах** той же стратегии;
  live-подтверждение идёт тем же механизмом (статистика + деградация
  гипотезы). Перевод варианта в ACTIVE — осознанный шаг
  (`store.transition(..., ACTIVE, reason)`), не автопромоция.
