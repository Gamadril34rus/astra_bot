# P1 — Zeus paper full trade cycle (до среза 26–27.09)

## Проблема

`ZeusTradeLog.entry/stop_adjust/exit` существовали, но `run_paper_zeus.py`
их не вызывал. State шёл в общие `models/paper_positions.json` /
`paper_trades.jsonl`, workflow их **не** сохранял → позиция жила один тик.

## Исправление (этот PR)

| # | Что | Где |
|---|-----|-----|
| 1.1 | После `engine.step` дифф брокера → `entry` / `stop_adjust` / `exit` | `scripts/run_paper_zeus.py` `sync_journal_from_broker` |
| 1.2 | Все path-поля → `zeus_*` | `TradingEngineConfig(...)` в runner |
| 1.3 | Save positions + trades | `.github/workflows/zeus-paper-clock.yml` |
| 1.4 | `ENVIRONMENT=paper` + `PAPER_TRADING=true` + abort если env live | workflow + runner |

### Изолированные пути (grep: ноль общих с основным ботом)

```
models/zeus_paper_positions.json
models/zeus_paper_trades.jsonl
models/zeus_strategy_stats.json
models/zeus_no_trade_observations.jsonl
models/zeus_no_trade_outcomes.json
models/zeus_pattern_exit_shadow.jsonl
models/zeus_halt_alerts.json
models/zeus_hypotheses.json
models/zeus_klines_cache/
```

### Не тронуто (правило 0)

`settings.yaml`, exit_plan, trading_engine.py, position_sizer, risk_engine,
веса/kill-switch/probation, bot.yml, статистика основного бота.

`reject` / `structure_state` / `ltf_impulse` — формат сохранён.

## 1.5 Аудит ущерба (окно бага)

По `models/zeus_trade_journal.jsonl` на момент фикса:

- `event=entry`: **0** (сделок в zeus-журнале не было)
- `structure_state.would_signal=true`: **0**
- `reject` с `has_wedge=true`: много (сетап не полный — `no_retest_close_inside`)

**Верхняя оценка «повторных открытий» за окно бага: 0**  
(не было ни одного `would_signal` → нечего было переоткрывать каждый тик).

Риск был архитектурный: при первом же полном сетапе позиция умерла бы
через 5 мин и могла открыться снова. После P1 позиция персистится.

## 1.4 Инвариант live

Workflow пишет только:

```
ENVIRONMENT=paper
PAPER_TRADING=true
```

Runner abort'ит, если `ENVIRONMENT` ∈ live/prod. Ордера на биржу
не отправляются (PaperBroker + paper env).

## Приёмка

Ждать первый полный сетап 4h: в journal `entry`, затем на следующих
тиках `open_positions>=1` в `tick`, при движении стопа — `stop_adjust`.
Владелец проверяет поиском `"event": "entry"` в journal.
