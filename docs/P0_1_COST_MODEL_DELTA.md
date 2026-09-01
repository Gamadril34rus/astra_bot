# P0-1: Единая модель издержек — отчёт о дельте

**Дата:** 2026-09-01
**ID задачи:** P0-1
**Приоритет:** P0 (критичный)

## Проблема

Три разные модели комиссий, одна из них — нулевая:
- Legacy PaperEngine: `fees=Decimal("0")` — без комиссий вообще
- PaperBroker (decision/broker.py): maker/taker 0.1% + slippage 0.1%
- ExecutionEngine: maker/taker 0.1% + slippage buffer 0.1%

В `models/paper_trades.jsonl` комиссии ≈ 0.1% только с одной стороны (round-trip 0.2% не учитывается).

## Решение

### Создан `astra_bot/engines/cost_model.py`

Единый класс `CostModel` (frozen dataclass):
- `taker_fee_rate`: Decimal (по умолчанию 0.001 = 0.1%)
- `maker_fee_rate`: Decimal (по умолчанию 0.001 = 0.1%)
- `slippage_pct`: Decimal (по умолчанию 0.001 = 0.1%)
- `funding_rate`: Decimal (для деривативов, опционально)

Методы:
- `effective_entry_price()` / `effective_exit_price()` — цены со slippage
- `entry_fee()` / `exit_fee()` — комиссии за сторону
- `round_trip_fees()` — полная комиссия за round-trip
- `net_pnl()` — PnL после всех издержек
- `check_round_trip_invariant()` — проверка что КАЖДАЯ сторона charged ≥ notional × min_fee_rate
- `assert_invariant()` — бросает ValueError при нарушении

Валидация: `__post_init__` запрещает `taker_fee_rate ≤ 0`. Это гарантирует что нулевые комиссии невозможны.

### Интеграция

| Компонент | Изменение |
|-----------|-----------|
| `PaperBroker` | Принимает `cost_model=` параметр; backward-compat с `fee_pct`/`slippage_pct`; использует CostModel для entry fill + exit fill + fees |
| `PaperTradingEngine` (legacy) | Принимает `cost_model=` (дефолт `CostModel()`); `_open_position()` теперь считает entry_fee через CostModel; `close_position()` добавляет exit_fee; запрет `fees=0` |

### Инвариант

Каждая сторона сделки: `fee ≥ notional × min_fee_rate`

Round-trip: `entry_fee + exit_fee ≥ (entry_notional + exit_notional) × min_fee_rate`

## Дельта «до/после» на исторических данных

### До (как было)

| Метрика | Значение |
|---------|----------|
| Всего сделок | 365 |
| Сделок с fees > 0 | **4** (1.1%) |
| Сделок с fees = 0 | **361** (98.9%) |
| Reported PnL | −4 086.03 USDT |
| Total fees recorded | ~12 USDT |
| Total notional | 1 427 793.54 USDT |

### После (оценка)

| Метрика | Значение |
|---------|----------|
| Commission (0.2% RT) | ~2 855.59 USDT |
| Slippage cost (0.2% RT) | ~2 855.59 USDT |
| **Total hidden costs** | **~5 711.17 USDT** |
| **Corrected PnL** | **~−9 797.20 USDT** |
| **Delta** | **−5 711.17 USDT** |

Реальный убыток был в **2.4 раза больше** зафиксированного.

## Тесты

### Новые тесты

| Файл | Тестов | Описание |
|------|--------|----------|
| `tests/unit/test_cost_model.py` | 22 | CostModel: prices, fees, PnL, invariant, from_flat |
| `tests/integration/test_cost_model_integration.py` | 10 | Legacy engine, PaperBroker, round-trip invariant, no-zero-fees |

### Существующие тесты (не сломаны)

| Набор | Результат |
|-------|-----------|
| `tests/unit` | ✅ 574 passed |
| `tests/integration` | ✅ 46 passed |
| **Total** | **620 passed** |

## Критерии приёмки

- [x] Прогон исторического среза `paper_trades` через пересчёт показывает PnL с полными издержками
- [x] Тесты `chaos/anti-leakage` зелёные (620 passed)
- [x] Отчёт о дельте «до/после» в `docs/` (этот документ)
- [x] `CostModel` — единый источник истины для комиссий и slippage
- [x] Legacy PaperEngine использует CostModel (запрет `fees=0`)
- [x] Комиссии считаются round-trip (вход+выход), slippage — по худшей стороне
- [x] Инвариант: каждая сторона ≥ notional × min_fee_rate
