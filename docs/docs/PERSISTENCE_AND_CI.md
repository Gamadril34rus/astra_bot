# Persistence и CI-гейты

Статус: реализовано (2026-08-28). Реализует TZ §28/§29/§35.

## Классификация state (TZ §28)

| Файл | Тип | Управление размером |
|---|---|---|
| `models/strategy_stats.json` | знание (bounded: стратегий×режимов×TF) | перезапись |
| `models/research/hypotheses.json` | знание (lifecycle) | append только новых гипотез |
| `models/research/observations.jsonl` | append-only archive | **rotation** |
| `models/no_trade_observations.jsonl` | append-only archive | **rotation** |
| `models/no_trade_outcomes.json` | runtime, bounded | pruning 30 дней |
| `models/decision_log.jsonl`, `live_lessons.jsonl`, `lessons.jsonl`, `paper_trades.jsonl`, `research_observations.jsonl` | append-only archive | **rotation** |
| `models/paper_*.json`, `risk_state.json`, `trading_budget.json`, ... | runtime (замена) | перезапись |

## Rotation (TZ §29)

`astra_bot/core/state_rotation.py`:

- каждый живой JSONL ограничен (5k–20k строк, `LIVE_JSONL_LIMITS`);
- при превышении в начале live-сессии (`scripts/run_bot.py`,
  `scripts/rotate_state.py`) вырезанные строки уходят в **append-only
  архив** `<имя>.archive.jsonl` — данные не удаляются, архив только
  растёт, рабочий файл ограничен;
- запись хвоста атомарная (tmp+rename).

**Size gate**: `scripts/check_state_size.py` (вызов в `bot.yml` после
каждой сессии) — exit 1, если живой JSONL превышает
`limit + 500 строк` (запас на сессию). Рост, не покрытый rotation,
ловится CI.

## Quality gates (TZ §35)

Новый workflow `.github/workflows/quality-gates.yml` (push master / PR):

1. **`pytest tests`** — полный suite (unit + integration, TZ §33/§34)
   с `--maxfail=1`;
2. **ruff на изменённые Python-файлы** — репозиторий имеет baseline
   ошибок в legacy-коде, поэтому гейт строгий к **новому/изменённому**
   коду (diff против base ref), а не ко всему репо;
3. **import smoke** — ключевые модули стека импортируются;
4. **state size gate** — `check_state_size.py`.

До Phase 5 ни один workflow не запускал тестовый suite — теперь код
не попадает в master без полного прогона.

## Ограничения (честно)

- **Git остаётся transport-механизмом state** между 5-минутными
  CI-сессиями (existing design `bot.yml`). Rotation ограничивает
  размер каждого state-файла, но история коммитов state в git
  продолжает расти. Полный уход в SQLite (TZ §28, «предпочтительно»)
  — отдельный следующий этап: план — `no_trade_observations` +
  outcomes первыми (SQLite, JSONL-экспорт остаётся archive), затем
  lessons/decision log.
- `models/` в `.gitignore`: state-файлы tracked через `git add -f`
  (existing convention) — новые файлы state при добавлении должны
  попадать в Save state `bot.yml`.

## Тесты

`tests/unit/test_state_rotation.py` — rotation (хвост/архив,
повторные прокрутки, целостность JSON, пропущенные файлы),
rotate_all по всем живым файлам, size gate (проход после rotation,
детекция роста), поведение скрипта `check_state_size.py` (exit 0/1).
