# Persistence и CI-гейты

Статус: реализовано (2026-08-28). Реализует TZ §28/§29/§35.

## Классификация state (TZ §28)

| Файл | Тип | Управление размером |
|---|---|---|
| `models/strategy_stats.json` | знание (bounded: стратегий×режимов×TF) | перезапись |
| `models/research/hypotheses.json` | знание (lifecycle) | append только новых гипотез |
| `models/research/observations.jsonl` | append-only archive | **rotation** |
| `models/no_trade_observations.jsonl` | append-only archive | **rotation** |
| `models/no_trade_outcomes.jsonl`, `models/zeus_no_trade_outcomes.jsonl` | append-once исходы с `id` | rotation 24k строк; индекс в памяти — 30 дней (мс) |
| `models/decision_log.jsonl`, `live_lessons.jsonl`, `lessons.jsonl`, `paper_trades.jsonl`, `research_observations.jsonl` | append-only archive | **rotation** |
| `models/paper_*.json`, `risk_state.json`, `trading_budget.json`, ... | runtime (замена) | перезапись |

## Rotation (TZ §29)

`astra_bot/core/state_rotation.py`:

- каждый живой JSONL ограничен (5k–24k строк, `LIVE_JSONL_LIMITS`);
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


## NO_TRADE outcomes: переход JSON → JSONL (11.10.2026)

- Одна строка: `{id, bar_time, symbol, reason_code, market_regime, horizons,
  computed_at, hypothetic_r?}`; `bar_time`/`timestamp` свечей — **миллисекунды**.
  Enrich дописывает только новые исходы, не переписывает индекс на каждом тике.
- Все читатели используют `astra_bot/ml/no_trade_outcomes.py`: JSONL первым,
  dedup **last-wins**; при отсутствии — legacy JSON wrapper или bare-map.
  Пустой/повреждённый, но существующий JSONL не подменяется устаревшим JSON.
- `_prune_outcomes` режет индекс в памяти до 30 дней в мс. На диске старые
  строки уходят в архив при ротации, не при каждом prune. `pending()` не
  переобогащает уже решённые или вышедшие из 30-дневного окна наблюдения.
- Zeus outcomes входит в `STATE_FILES` и `LIVE_JSONL_LIMITS`: ротация хранит
  весь хвост **24 000**, не общий хвост 20k (лимит не меньше наблюдений).
  Близнец ограничен теми же 24k в общем ротаторе, но **заморожен** с 09.10;
  миграция — на будущее при реактивации, не включение торговли.

Миграция обоих файлов одним скриптом:

```sh
python scripts/migrate_no_trade_outcomes.py --dry-run
python scripts/migrate_no_trade_outcomes.py
# Обязательно явно закоммитить ПОЛНЫЕ снимки исходных JSON:
git add -f models/archive/*no_trade_outcomes_*.json.gz
git add -f models/zeus_no_trade_outcomes.jsonl models/no_trade_outcomes.jsonl
git add -u models/zeus_no_trade_outcomes.json models/no_trade_outcomes.json
```

Скрипт сортирует по `bar_time`, `computed_at` (id разрешает точные совпадения),
сохраняет полный byte-for-byte gz-снимок **до** удаления legacy JSON и оставляет
24k последних исходов. Повторный запуск не меняет JSONL/архивы, включая новые
append после миграции; отличный существующий JSONL не перезаписывается.

**Rollout:** `.github/**` этой миграцией не меняется. Замена путей в Save-state
YML — отдельный мини-PR после её мержа. Допустимо окно **5–15 минут без коммита
новых outcomes** до YML-патча; код продолжает писать JSONL. Runtime workflows
не коммитят `models/archive/`: без явного `git add -f` снимки эфемерны.

**Digest:** исправлены wrapper и ошибочное умножение `bar_time` ещё на 1000:
теперь NO_TRADE-выводы попадают в реальное окно watermark/26 часов. Старые
исходы больше не выглядят свежими, сохраняется прежний вердикт по направлению.
