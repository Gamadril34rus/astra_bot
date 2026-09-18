# Zeus auto-shadow — проверка до среза без live-риска

**Проблема:** идеи (Зевс и др.) лежали в коде, но основной бот их не гонял.

**Решение:** на каждом `TradingEngine.process_symbol` тень оценивает
`zeus_wedge_retest_4h` на закрытых 4h-барах и пишет гипотетический сигнал
в `models/zeus_signal_shadow.jsonl`. **Исполнения нет** (`would_execute: false`).

## Конфиг (default ON)

```python
zeus_signal_shadow_enabled: bool = True
zeus_signal_shadow_path: str = "models/zeus_signal_shadow.jsonl"
```

Выключить: `TradingEngineConfig(zeus_signal_shadow_enabled=False)`.

## Что копится до 26–27.09

- число срабатываний / день
- direction long/short
- entry / stop / tp / rr
- features (pattern, границы клина)

На срезе: если n≥5 и исходы (после обогащения ценой) дают PF≥1 → кандидат
на probation ×0.25. Иначе — оставить research.

## Не путать

| Журнал | Что |
|--------|-----|
| `htf_shadow_bans.jsonl` | запрет направления против 4h EMA |
| `zeus_signal_shadow.jsonl` | **сигнал** клина Зевса (этот модуль) |
| `zeus_trade_journal.jsonl` | paper-clock runner (`run_paper_zeus.py`) |

Live / `settings.yaml` / `enabled` стратегии в prod — **без изменений**.
