# Zeus paper-clock — завод «как часики» (research, no live)

**Цель:** один контур Зевса на paper, измеримый и повторяемый.  
**Не цель:** мгновенный VIP-профит. Live до среза 26–27.09 не меняем.

---

## Механизм

```
4h клинья → ложный выход → ретест → close внутрь → вход
стоп за экстремумом ложного пробоя
+1R (MFE) → SMART BREAKEVEN
дальше → structural_stop / trailing ATR
журнал → models/zeus_trade_journal.jsonl
```

| Компонент | Где |
|---|---|
| Сигнал | `astra_bot/strategies/zeus_wedge_retest.py` |
| Paper runner | `scripts/run_paper_zeus.py` |
| Профиль yaml | `config/paper_zeus_clock.yaml` (не prod) |
| Журнал | `astra_bot/decision/zeus_trade_log.py` |
| Сопровождение | `ExitController` SMART + `structural_stop` в TradingEngine |

Prod default стратегии: **`enabled=False`**. Runner включает только у себя.

---

## Запуск

```bash
# Один цикл (проверка связи / decide)
python scripts/run_paper_zeus.py --once

# Непрерывный paper-clock (BTC-USDT, poll 300s)
python scripts/run_paper_zeus.py

# Другой символ / журнал
python scripts/run_paper_zeus.py --symbol ETH-USDT --journal models/zeus_eth.jsonl
```

`settings.yaml` / `trading_enabled` **не** читаются этим скриптом как источник списка стратегий — в pipeline кладётся только Зевс.

---

## Что писать в журнал (схема event)

- `clock_start` — старт runner
- `entry` — reason + features (pattern, upper/lower, width)
- `stop_adjust` — why (breakeven / structure / trail)
- `exit` — reason + r_multiple

Сигнал Зевса кладёт в `Signal.features`:
- `zeus_pattern`, `zeus_reason`
- `wedge_upper` / `wedge_lower`
- `stop_structure`: `beyond_false_break_extreme`

---

## Критерии «часики тикают»

1. Сделки только с `strategy_name=zeus_wedge_retest_4h`
2. Нет параллельных mean_reversion / scalp в этом runner
3. Стоп при входе за экстремумом пробоя (не «в воздухе»)
4. После MFE≥~0.8R стоп уходит в нетто-БУ (SMART)
5. n≥5 paper → смотреть PF; promote только post-slice по memo

---

## Чего не делать

- Не копировать `enabled: true` в production `settings.yaml` до среза
- Не мешать 5m-статы с этим ключом
- Не поднимать risk% ради частоты
- Не крутить lookback/min_rr каждый день под последние 5 сделок
