# Trade ledger (P1.8)

Append-only JSONL `models/paper_ledger.jsonl` (путь: `ASTRA_LEDGER_PATH`).

Каждая мутация денег/позиции — строка со `schema_version=1`:

| kind | смысл |
|---|---|
| `order` | принятый ордер (ещё без инвентаря) |
| `fill` / `partial_fill` | исполнение; `position_delta` и при закрытии `cash_delta=pnl` |
| `fee` | комиссия отдельной строкой |
| `cancel` / `rejection` | отказ |
| `reconciliation` | ручная сверка |

`TradeLedger.replay()` восстанавливает cash и позиции без памяти процесса. Paper-контур: cash = initial + Σ pnl закрытий (как `PaperBroker.equity`).

Идемпотентность: `event_id` вида `{kind}:{pos.id}:{open|close}`. Повтор той же записи не дублирует строку.

Отсутствующий файл: `append` создаёт каталог/файл. Сбой записи — `record_best_effort` (fail-open, торговля не падает).

Ротация: `astra_bot/core/state_rotation.py` лимит **20_000** строк (`paper_ledger.jsonl`), отдельно от `paper_trades.jsonl` (**10_000**). Хвост в `paper_ledger.archive.jsonl`.
