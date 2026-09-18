# Zeus auto-shadow — проверка до среза без live-риска

**Проблема:** идеи лежали в коде, основной бот их не гонял.

**Решение:** `install_on_engine` вешается на `process_symbol` из:
- `scripts/run_bot.py` (GitHub Actions каждые 5 мин)
- `astra_bot/main.py` (AstraBot modern path)

На каждом тике: evaluate `zeus_wedge_retest_4h` на закрытых 4h →
`models/zeus_signal_shadow.jsonl`. **`would_execute: false`** — в риск не идёт.

## После merge

Каждая сессия `run_bot.py` пишет в state. Save-state в bot.yml сохранит
jsonl в git — к срезу накопится n.

На срезе 26–27.09: n≥5 + исходы → probation ×0.25 или оставить research.

## Не путать

| Файл | Смысл |
|------|--------|
| `zeus_signal_shadow.jsonl` | гипотетические **входы** Зевса |
| `htf_shadow_bans.jsonl` | запрет направления против 4h EMA |
| `zeus_trade_journal.jsonl` | отдельный paper-clock runner |

`settings.yaml` / risk% / live — **без изменений**.
