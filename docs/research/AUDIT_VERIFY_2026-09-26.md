# Audit verification — 2026-09-26 (step 1, no code changes)

Источник: внешний technical audit. Порядок: **верификация → фиксы**.
Это проверка фактов по `master` на момент отчёта. CI-хирургия не тронута.

---

## (F1) ENVIRONMENT-защита в `scripts/run_paper_zeus.py`

**Вердикт: #101 прав; аудитор неправ.**

Строка **есть**. Блок в `amain()`:

```python
# scripts/run_paper_zeus.py:300-308
env = (os.environ.get("ENVIRONMENT") or "").strip().lower()
paper_flag = (os.environ.get("PAPER_TRADING") or "").strip().lower()
if env and env not in ("paper", "test", "dev"):
    logger.error("ABORT: ENVIRONMENT=%s", env)
    return 2
if not env:
    os.environ["ENVIRONMENT"] = "paper"
if paper_flag not in ("1", "true", "yes"):
    os.environ["PAPER_TRADING"] = "true"
```

Поведение:
- `ENVIRONMENT` ∈ {paper, test, dev} или пусто → ок (пусто → force `paper`);
- любое другое значение → **ABORT return 2**;
- `PAPER_TRADING` дописывается в true, если не 1/true/yes.

**Фикс F1 не нужен.**

---

## (C2) `disabled_strategy_names` в pipeline?

**Вердикт: частично. Фильтр есть, но не там, где сказал аудитор.**

| Место | Есть фильтр? |
|-------|----------------|
| `DecisionPipeline.decide()` | **нет** |
| `DecisionPipeline._candidates_from_strategies()` | **нет** (все `self.strategies` вызываются) |
| `TradingEngine.process_symbol` после `decide()` | **да** |

Конфиг и пост-фильтр:

```python
# trading_engine.py ~196 — denylist в TradingEngineConfig
disabled_strategy_names: frozenset[str] = frozenset({
    "zscore_mean_reversion", "liquidity_sweep", ...
})

# trading_engine.py ~1432-1436 — после decision.candidate
_sname = str(getattr(cand, "strategy_name", "") or getattr(cand, "strategy", "") or "")
_dis = getattr(self.config, "disabled_strategy_names", frozenset())
if _sname and (_sname in _dis or any(d.lower() in _sname.lower() for d in _dis)):
    logger.info("%s: strategy disabled by sprint denylist: %s", symbol, _sname)
    return closed
```

Следствие: disabled-стратегии **всё ещё генерируют кандидатов** в pipeline (шум в diagnostics / NO_TRADE path), но **вход блокируется** в engine.
Жёстче (как в формулировке аудита) было бы фильтровать в `_candidates_from_strategies` — это отдельный PR, не «восстановление отсутствующего».

---

## (B6) `pos.highest_price` / `lowest_price` по барам в `process_symbol`?

**Вердикт: пересчёт есть; посева нет (намеренно); покрытие — только `last_bar`.**

1. **Посев при open запрещён** (комментарий + код):

```python
# broker.py ~567-574
# highest_price/lowest_price здесь НЕ инициализируются.
# … посев подставил бы цену входа … стоп начал бы подтягиваться на баре открытия.
```

Согласовано с ТЗ шага 2: «посев НЕ возвращать — только пересчёт».

2. **Пересчёт каждый тик `process_symbol`:**

```python
# trading_engine.py ~1237-1241
last_bar = primary[-1]
self.broker.update_extremes(last_bar)
```

```python
# broker.py update_extremes ~666-678
high = Decimal(str(bar.high))
low = Decimal(str(bar.low))
…
pos.highest_price = high if pos.highest_price is None else max(pos.highest_price, high)
pos.lowest_price = low if pos.lowest_price is None else min(pos.lowest_price, low)
```

3. **Ограничение:** обновляется **только `primary[-1]`**, не цикл по всем барам с прошлого тика. При gap между сессиями CI промежуточные closed-бары не реплеятся → MFE/MAE по high/low могут быть **занижены** относительно полной серии.
Это согласуется с решением владельца: **TP-план C со среза снять до пересчёта MFE** (пререгистрация на заниженных данных).

**Минимальный fix B6 (если делать):** в `process_symbol` прогнать `update_extremes` по всем барам новее последнего обработанного `open_time` (не только `[-1]`). Посев не трогать.

---

## Краткие наблюдения по шагу 2 (не фикс, ориентир)

| ID | Статус на master (spot-check) |
|----|-------------------------------|
| **E4** probation ×0.25 | **Уже есть** (`sample_size < 5` → qty×0.25; binding `probation`) |
| **E3** kill n≥5 PF&lt;1 | **Частично:** `apply_kill_switches_from_stats` снимает из `pipeline.strategies` на init; **не** пишет persistent disabled/blacklist в `_record_closed` |
| **A4** `kill_switch_state.json` в Save state | Файл/класс есть (`DEFAULT_STATE_PATH`); **в bot/zeus Save state списках не найден** |
| **A5** SymbolLossGuard persist | `_losses` / `_pause_until` **только in-memory**; to_dict/Save state **нет** |
| **C4** zeus_* в Save state | **Уже есть** в zeus-paper-clock: positions, trades, strategy_stats, halt_alerts |
| **B5** tp_fractions [0.3,0.7] | **Уже** `PLAN_TP_FRACTIONS = (0.3, 0.7)` в exit_plan |
| **A1/A2** cancel-in-progress / pull \|\| true | zeus/bot: `cancel-in-progress: false`; `git pull … \|\| true` — **как в аудите** |

---

## DECISION_MEMO (черновик строки на шаг 3)

> Защиты E3/E4/A4/A5 до 26.09: E4 и часть E3 (session kill из stats) уже в коде; A4/A5 persist и полный E3 blacklist — только в README / не в Save state. После включения persist — окно замера перезапустить с &lt;дата&gt;.
> TP-план C со среза **снят** до fix B6 (replay extremes по gap-барам) — пререгистрация на заниженном MFE.

---

## Следующий шаг

После approve этого отчёта — фиксы **крит** отдельными PR + тест каждый; группы логические; полный pytest/ruff; **без CI-хирургии**.
