# Research Memory (типизированная память)

Статус: реализовано (2026-08-28). Реализует TZ §14, §26, §30.

## Разделение по типам (TZ §14)

| Тип | Хранилище | Формат |
|---|---|---|
| **OBSERVATIONS** | `models/research/observations.jsonl` | append-only JSONL |
| **HYPOTHESES** | `models/research/hypotheses.json` | JSON (HypothesisStore) |
| **STRATEGIES** | `models/strategy_stats.json` | JSON (StrategyStatsStore) |
| **LESSONS** | `models/lessons.jsonl`, `models/live_lessons.jsonl` | append-only JSONL |
| **MODELS** | `models/registry.json` + `models/vNNN/` | Model Registry (Phase 4) |

Одного «всё в одном JSON» нет: каждый тип — своё хранилище со своей
семантикой (append-only журнал против версиируемого объекта против
матрицы статистики).

## Схема записи наблюдения (TZ §14)

```json
{
  "id": "a1b2c3… (sha1, стабильный)",
  "timestamp": "2026-08-28T20:40:00Z",
  "bar_time": 1700000000,
  "type": "market_research_observation",
  "kind": "research_event | live_no_trade | live_trade | experiment",
  "source": "market_research | trading_engine | backtest",
  "version": 1,
  "symbol": "BTC-USDT",
  "features": {…},
  "forward": {…},
  "confidence": 0.4,
  "sample_size": 1
}
```

`id = sha1(source|symbol|bar_time|kind|digest(features))[:20]` —
повторная обработка того же события **не создаёт дубля** (idempotency,
TZ §30; покрыто тестом, включая «перезапуск процесса» — known-ids
перечитываются из файла).

## Подключение

`ResearchMemory` — фасад (`astra_bot/ml/research_memory.py`):
`record_observation(...)`, `count()`, `.hypotheses` (HypothesisStore).
Legacy-архив `models/research_observations.jsonl` (market_research)
остаётся историческим; новые записи идут в `models/research/`.

## Тесты

`tests/unit/test_research_memory.py` — схема (все обязательные поля),
dedup одного input, разные features → разные id, dedup после перезапуска,
стабильность id, привязка HypothesisStore, JSONL-архив.
