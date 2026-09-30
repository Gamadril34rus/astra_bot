# Внешний аудит astra_bot (от А до Я)

**Дата:** 2026-09-30 (по ТЗ имя файла 2026-10-06)  
**Репозиторий:** Gamadril34rus/astra_bot @ `0d56f0a` (master)  
**Режим аудита:** только чтение, без правок кода и без PR  
**Объём выборки сделок:** `models/paper_trades.jsonl` — **241** закрытая сделка  

Каждый факт ниже: `файл:строка` + цитата ≥3 строк + вердикт.

---

## A. Инфраструктура и жизненный цикл

### A1. Два воркфлоу пушат в `master` каждые 5 минут (гонка)

**Severity:** ВАЖНО  

```yaml
# .github/workflows/bot.yml (cron + Save state)
on:
  schedule:
    - cron: "*/5 * * * *"
concurrency:
  group: astra-bot
  cancel-in-progress: false
# ...
          git push origin master || echo "push failed (will retry next run)"
```

```yaml
# .github/workflows/zeus-paper-clock.yml
on:
  schedule:
    - cron: "*/5 * * * *"
concurrency:
  group: zeus-paper-clock
  cancel-in-progress: false
# ...
          git push origin master || echo "push failed (will retry next run)"
```

**Вердикт:** риск.  
`concurrency.group` **разные** → bot и Zeus могут одновременно `git pull` / `commit` / `push` в один `master`. Один push может быть отклонён (`|| echo` глотает ошибку), состояние одного контура теряется до следующего цикла. A2 снял `|| true` только с **pull**, не с **push**.

**Что это значит для денег:** возможна «амнезия» открытых позиций / журнала на один–несколько тиков → повторные входы или пропуск стопов после рестарта.  
**Минимальный фикс:** один `concurrency.group` на оба воркфлоу **или** отдельные ветки/артефакты для Zeus state; push без «тихого» успеха при reject.

### A2. Zeus: ошибка тика не красит CI

**Severity:** ВАЖНО  

```yaml
# .github/workflows/zeus-paper-clock.yml
      - name: Zeus paper-clock --once multi-symbol
        run: python scripts/run_paper_zeus.py --once --symbols ...
        continue-on-error: true
      - name: Telegram status
        run: python scripts/zeus_telegram_status.py ...
        continue-on-error: true
```

**Вердикт:** риск.  
Падение paper-тика Зевса выглядит как зелёный Actions; владелец видит только отсутствие карточек в Telegram.

**Что это значит для денег:** молчаливый простой research-контура на дни.  
**Минимальный фикс:** убрать `continue-on-error` с шага paper-clock (status можно оставить soft).

### A3. PaperBroker.save — неатомарная запись

**Severity:** ВАЖНО  

```python
# astra_bot/decision/broker.py:323-356
    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "positions": [
                {
                    **asdict(p),
                    "entry_price": str(p.entry_price),
                    # ...
                }
                for p in self.positions
            ],
            # ...
        }
        self.state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
```

Сравнить с kill-switch (атомарно):

```python
# astra_bot/core/kill_switch.py:262-274
    def save(self) -> None:
        """Atomic write (tmp + replace). No-op if state_path is None."""
        # ...
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(...)
            tmp.replace(path)
```

**Вердикт:** баг/риск.  
Обрыв процесса посреди `write_text` → битый `paper_positions.json` → потеря позиций или пустой state после pull.

**Что это значит для денег:** после сбоя CI позиция может «исчезнуть» из учёта, хотя логика думала, что она открыта.  
**Минимальный фикс:** тот же паттерн `tmp + replace`, что у `SymbolLossGuard`.

---

## B. Исполнительный контур

### B1. Геометрия тейка 2.0–2.3R vs реальность MFE (главная «денежная» дыра)

**Severity:** КРИТ (для PnL paper; не live-ордер)  

```python
# astra_bot/decision/exit_plan.py:81-83, 116-122
    take_rr_min: float = 2.0
    take_rr_max: float = 2.3
# ...
def clamp_take_rr(..., rr_min: float = 2.0, rr_max: float = 2.3):
```

Факт по леджеру (241 сделка):

| Метрика | Значение |
|---------|----------|
| mean R | **−0.271** |
| median MFE | **0.281 R** |
| MFE ≥ 2.0R | **5 / 241 (~2%)** |
| MFE ≥ 0.7R | **58 / 241 (~24%)** |
| exit stop_loss | **184 / 241 (~76%)** |

**Вердикт:** противоречие канона исполнения с рынком.  
Код честно клампит тейк в 2.0–2.3R; цена почти никогда туда не доходит. Memo уже знает план C (0.70R), но **в бою всё ещё 2.0–2.3**.

**Что это значит для денег:** системный отрицательный expectancy (~−0.27R/сделку) при текущей геометрии.  
**Минимальный фикс:** не «подкручивать риск», а сменить план тейка по memo (C) **после** явного решения владельца на срезе; до среза — не менять окно.

### B2. Исключения вокруг ликвидаций и forced-выходов глотаются

**Severity:** ВАЖНО  

```python
# astra_bot/decision/trading_engine.py:1389-1410
        closed = self.broker.check_exits(last_bar)
        try:
            await self._sync_perps_state(symbol, last_bar.close)
            liq_closed = self.broker.check_liquidations(symbol)
            closed = closed + liq_closed
        except Exception as exc:
            logger.debug("liquidation check %s: %s", symbol, exc)
        try:
            closed = closed + self.plan_engine.forced_closes(...)
        except Exception as exc:
            logger.debug("plan forced_closes: %s", exc)
```

**Вердикт:** риск.  
Любой сбой sync mark / forced → только `debug`, тик идёт дальше **без** ликвидации/MAX_HOLD на этом баре.

**Что это значит для денег:** в экстремуме позиция живёт дольше задуманного.  
**Минимальный фикс:** `logger.warning` + метрика; при N сбоях подряд — HALT-алерт в TG.

### B3. `apply_tighter` — стоп только сжимается (ок)

**Severity:** ок  

```python
# astra_bot/decision/exit_plan.py:267-284
def apply_tighter(pos: Any, candidate: Decimal | None) -> bool:
    """Применить кандидата стопа, если он уменьшает риск.
    ...
    """
    if direction == "long" and candidate > pos.stop_loss:
        pos.stop_loss = candidate
        return True
    if direction == "short" and candidate < pos.stop_loss:
        pos.stop_loss = candidate
        return True
    return False
```

**Вердикт:** ок — инвариант «стоп не расширяется» соблюдён в коде.

### B4. R2-01/R2-02: watermark bars_held + funding по 5m (ок после фикса)

**Severity:** ок (после R2)  

```python
# astra_bot/decision/broker.py:45-51, 351-356, 1012-1025
FUNDING_BAR_MINUTES = 5
# ...
            "last_extremes_bar": dict(self._last_extremes_bar),
# ...
        held_minutes = Decimal(pos.bars_held) * Decimal(FUNDING_BAR_MINUTES)
```

**Вердикт:** ок относительно известного бага «фандинг ×48 на 4h».

### B5. BingX live-ордера явно запрещены (ок)

**Severity:** ок  

```python
# astra_bot/adapters/bingx/client.py:837-851
    # === Ордера/позиции: live отключён (paper-контур через PaperBroker) ===
    async def place_order(...) -> Order:
        """Разместить ордер — НЕ РЕАЛИЗОВАНО (live отключён)."""
        raise NotImplementedError(_LIVE_DISABLED_MSG)
```

**Вердикт:** ок для BingX-пути `run_bot` / Zeus.

### B6. OKX `place_order` реализован (скрытый live-путь)

**Severity:** ВАЖНО (условный КРИТ, если кто-то переключит адаптер)  

```python
# astra_bot/adapters/okx/client.py:618-660
    async def place_order(...):
        # ...
            data = await self._request("POST", OKX_ENDPOINTS["spot"]["place_order"], body=body, signed=True)
```

**Вердикт:** риск.  
Paper-вход через BingX безопасен; полный клиент OKX умеет слать ордера. Нет единого «global paper lock» на все адаптеры.

**Что это значит для денег:** ошибка конфигурации «exchange=okx + keys» может уйти в live.  
**Минимальный фикс:** в `place_order` OKX — тот же `NotImplementedError`, пока `ENVIRONMENT!=live` и нет явного флага владельца.

### B7. Лестница плеча + формула «стоп раньше ликвидации» (ок с оговоркой)

**Severity:** ок / косметика на грани  

```python
# astra_bot/decision/trading_engine.py:67-116
LEVERAGE_LADDER: tuple[tuple[float, float, int], ...] = (
    (0.95, 3.0, 100),
    ...
)
    if entry > 0:
        d = abs(entry - stop) / entry
        if d > 0:
            feasible = int(1.0 / (d + maintenance_margin_pct)) - 1
            if lev > feasible:
                lev = max(1, feasible)
```

**Вердикт:** ок по замыслу.  
При очень узком стопе (d→0) `feasible` велик → 100x теоретически допустим; узкий стоп + costs_r — отдельная экономика (см. F).

---

## C. Генерация сигналов и risk-статистика

### C1. Kill-switch n≥5 / PF<1 (ок, с fail-open при ошибке чтения)

**Severity:** ок + лёгкий риск  

```python
# astra_bot/decision/trading_engine.py:822-873
    def apply_kill_switches_from_stats(self) -> list[str]:
        """...
        n>=5 и PF=wins/|losses|<1.0 → убрать стратегию из pipeline.strategies
        """
        # ...
                if a["n"] >= 5 and a["l"] < 0 and (a["w"] / abs(a["l"])) < 1.0:
                    self.pipeline.strategies = [
                        st for st in self.pipeline.strategies
                        if getattr(st, "name", type(st).__name__) != name
                    ]
```

Вызов:

```python
# trading_engine.py:408-410
            self.apply_kill_switches_from_stats()
        except Exception as exc:
            logger.debug("kill-switch: %s", exc)
```

**Вердикт:** правило в коде совпадает с README; при битом `strategy_stats.json` kill-switch **молча** не срабатывает (fail-open).

**Минимальный фикс:** при ошибке чтения stats — `warning` + не добавлять новые стратегии (или HALT-алерт).

### C2. Probation ×0.25 (ок)

**Severity:** ок  

```python
# astra_bot/decision/trading_engine.py:775-787
            probation = bucket is None or bucket.sample_size < 5
            qty = calculate_position_size(...)
            return qty * (Decimal("0.25") if probation else Decimal("1"))
```

**Вердикт:** ок.  
`None`/пустой bucket → probation (не ×1.0). Fallback при полном exception сайзера **без** ×0.25 — отдельный путь «аварийного» размера 1% риска (строки 788–801).

### C3. Wins: `r_multiple > 0` (ноль = лосс)

**Severity:** КОСМЕТИКА / учёт  

```python
# astra_bot/decision/strategy_stats.py:79-90
        if r_multiple > 0:
            self.wins += 1
            self.wins_sum_r += r_multiple
        else:
            self.losses += 1
            self.losses_sum_r += r_multiple
```

**Вердикт:** ок для осторожности.  
Сделки ~0R (после комиссий) идут в losses — PF чуть пессимистичнее, kill-switch чуть жёстче. На 241 сделке `r<=0` — 184.

### C4. entry_gates в тени (канон)

**Severity:** ок (осознанно)  

```python
# trading_engine.py:183-192
    entry_gates_enabled: bool = False
    entry_gates_shadow_enabled: bool = True
```

**Вердикт:** ок относительно memo «тень до критерия».

### C5. Cooldown 20 мин, ключ strategy|symbol|side, персист в state (ок)

**Severity:** ок  

```python
# trading_engine.py:180-181, 804-808
    reentry_cooldown_minutes: int = 20
    def _cooldown_key(strategy: str, symbol: str, side: str) -> str:
        return f"{strategy}|{symbol}|{side}"
```

```python
# broker.py:350, 338-347
            "cooldowns": self._purge_cooldowns(),
```

**Вердикт:** ок; обход сменой **стратегии** на том же символе/стороне **возможен по дизайну ключа** (не баг, а ослабление анти-дребезга между стратегиями).

---

## D. Данные и биржа

### D1. Сигналы/выходы по high/low бара (внутрибарно)

**Severity:** ВАЖНО (модельный)  

```python
# astra_bot/decision/broker.py:702-720
    def check_exits(self, bar) -> list[ClosedTrade]:
        high = Decimal(str(bar.high))
        low = Decimal(str(bar.low))
        # ...
            stop_hit = (
                (pos.direction == "long" and low <= pos.stop_loss)
                or (pos.direction == "short" and high >= pos.stop_loss)
            )
```

**Вердикт:** риск моделирования.  
Нет явной пометки «бар закрыт»; paper считает, что high/low уже известны. На 5m CI это близко к post-factum OHLC. Lookahead на незакрытом баре возможен, если API отдаёт текущую свечу.

**Минимальный фикс:** отбрасывать бар с `open_time + duration > now` (только closed).

---

## E. Учёт и метрики

### E1. Арифметика леджера согласуется с «известными» цифрами

**Severity:** ок (как диагностика)  

По `paper_trades.jsonl` (n=241): mean R ≈ **−0.27**, median MFE ≈ **0.28** — совпадает с историческим контекстом аудита. Fees/funding присутствуют в записях.

Пример «странный» stop с почти нулевым R при MFE 0.84R (комиссии съели ход):

```text
# models/paper_trades.jsonl (ADA-USDT, индекс ~120)
exit_reason=stop_loss, r_multiple≈9e-05, fees≈3.43, mfe_r≈0.84
```

**Вердикт:** не баг записи; иллюстрация costs_r при узком стопе.

### E2. MFE/MAE обновляются по барам (B6), не только на выходе

**Severity:** ок после B6  

```python
# broker.py:683-700
    def update_extremes(self, bar) -> None:
        pos.highest_price = max(...)
        pos.lowest_price = min(...)
```

Движок доигрывает gap-бары (B6) в `process_symbol` / `_process_exits_only`.

---

## F. Контур «Зевс»

### F1. Изоляция путей state (ок)

**Severity:** ок  

Zeus Save state пишет `models/zeus_*`; main — `models/paper_*` / общий `symbol_loss_guard` только в bot.yml.  
`run_paper_zeus.py` задаёт отдельные paths (по коду раннера). ENVIRONMENT=paper прописывается в workflow.

### F2. min_stop_pct 0.8% на wedge — floor есть

**Severity:** ок / важно для channel  

```python
# astra_bot/strategies/zeus_wedge_retest.py:47-52, 91
def _enforce_min_stop(...):
    ...
    min_stop_pct: float = 0.008
```

**Вердикт:** floor 0.8% есть у wedge; историческая боль «0.7–1.6% stop у channel → costs_r 0.19–0.38R» требует отдельной проверки **channel**-стратегии (если stop без floor — ВАЖНО). Wedge сам по себе не «голый» 0.3% stop.

### F3. Zeus notifier=None (карточки сделок основного контура не шлёт)

**Severity:** ок  
Paper Zeus не включает `_trade_cards_enabled` (по дизайну этапа 4).

---

## G. Документы и процесс

### G1. DECISION_MEMO vs код: min_rr / clamp согласованы; план C не включён

**Severity:** ВАЖНО (разрыв research→prod)  

Memo (2026-09-26): PRIMARY план **C — один тейк 0.70R**; baseline CURRENT 2.0–2.3 «недостижим как массовая цель».  
Код: `take_rr_min/max = 2.0/2.3`, `cfg.min_rr = 2.0`.

**Вердикт:** противоречие «истина memo» vs «истина исполнения». Не ложь memo — **неисполненное решение**.

### G2. README kill-switch / probation совпадает с кодом

**Severity:** ок  

README описывает n≥5, PF<1, probation ×0.25 — совпадает с `apply_kill_switches_from_stats` и сайзингом.

### G3. Массовые `chore(ci): bot state` в master

**Severity:** КОСМЕТИКА / процесс  

Десятки коммитов state каждые ~5 мин. Живой код через PR (по истории R2/A5/B6) — соблюдается лучше, чем «one-shot без ревью», но **шум git** и риск гонки (A1).

### G4. Секреты в git

**Severity:** ок (по выборочному grep)  
Реальные ключи в tracked-файлах не найдены; `.env` собирается из GitHub Secrets.  
`docker-compose.yml` содержит дефолтные placeholder-пароли (`secure_password_change_me`) — не боевые ключи биржи.

---

## 3. Красные флажки (сводка)

| # | Флажок | Итог |
|---|--------|------|
| 1 | Live-ордер из paper | BingX — закрыт; OKX — открыт в коде адаптера |
| 2 | R до/после издержек | В ClosedTrade r_multiple net; stats на net R |
| 3 | Незакрытый бар | Нет жёсткого фильтра closed-only |
| 4 | except рядом с деньгами | liquidation / forced / kill-switch init — debug |
| 5 | README/memo vs код | План C vs clamp 2.0–2.3 |
| 6 | Петля самообмана stats | Kill-switch читает тот же stats, что пишет бот — ожидаемо; риск только при порче файла |
| 7 | Гонка workflow | **Да**, два group → два push в master |
| 8 | Магические числа | В целом комментированы (лестница, clamp, 0.25) |
| 9 | Cooldown обход | Другая стратегия = другой ключ |
| 10 | Probation → ×1.0 от None | **Нет**, None → 0.25 |

---

## 5. Финальный раздел

### 1. Топ-5 самых дорогих проблем

| # | Проблема | Оценка влияния |
|---|----------|----------------|
| 1 | **Тейк 2.0–2.3R при median MFE 0.28R** | ~**−0.27 R/сделку** на 241 сделке; при 6 сделках/день × ~20 дней ≈ порядок **−30 R/месяц** paper (грубо, без новых фильтров) |
| 2 | **Гонка bot+Zeus push в master** | Редкие, но дорогие «дыры» state (пропуск стопа / двойной учёт) |
| 3 | **Неатомарный `PaperBroker.save`** | Коррупция positions при kill CI |
| 4 | **except:debug на liquidation/forced** | Хвост риска на падении mark sync |
| 5 | **OKX live place_order в том же репо** | Условный live при смене адаптера |

### 2. Что работает хорошо

1. BingX live-ордера сознательно `NotImplementedError`.  
2. Kill-switch n≥5 / PF<1 и probation ×0.25 реализованы и задокументированы.  
3. `apply_tighter` не расширяет стоп.  
4. R2-01/02 (watermark bars_held, funding 5m) закрывают известный ×48 фандинг.  
5. R2-03/04 (выходы при паузе/лимите, карточки независимо от guard) — правильное направление защиты.  
6. Cooldown после стопа персистится в state.  
7. SymbolLossGuard пишет state атомарно.  
8. Memo честно фиксирует MFE/план C и запрет третьего ретюна окна.  
9. Entry gates в тени до решения владельца — нет самовольного «включения».  
10. Раздельные `zeus_*` файлы state от main paper.

### 3. Что проверить срезом (нет ответа только кодом)

1. Включать ли **план C (0.70R)** + entry_gates ON одним бандлом (как memo)?  
2. Какова доля сделок channel vs wedge у Зевса и median stop_pct channel?  
3. Сколько раз за месяц `git push origin master` из CI был rejected?  
4. Есть ли в API BingX отдача **незакрытой** 5m-свечи в последнем элементе klines?  
5. Нужен ли единый paper-lock на **все** биржевые адаптеры до live-решения?

### 4. Индекс находок

| ID | Файл:ориентир | Severity | Суть |
|----|---------------|----------|------|
| A1 | `.github/workflows/bot.yml` + `zeus-paper-clock.yml` | ВАЖНО | Два push в master каждые 5 мин, разные concurrency group |
| A2 | `zeus-paper-clock.yml` continue-on-error | ВАЖНО | Тихий провал Zeus-тика |
| A3 | `broker.py:323` save | ВАЖНО | Неатомарная запись positions |
| B1 | `exit_plan.py:81-83` + леджер | КРИТ | Тейк 2–2.3R vs MFE~0.28R → −0.27R mean |
| B2 | `trading_engine.py:1392-1410` | ВАЖНО | except debug на liq/forced |
| B3 | `exit_plan.py:267` | ок | Стоп только tighter |
| B5 | `bingx/client.py:837` | ок | Live BingX выключен |
| B6 | `okx/client.py:618` | ВАЖНО | Live place_order существует |
| C1 | `trading_engine.py:822` | ок/риск | Kill-switch; fail-open на ошибке чтения |
| C2 | `trading_engine.py:775` | ок | Probation ×0.25 |
| C5 | cooldown key | ок | 20 мин, персист |
| D1 | `broker.py:702` | ВАЖНО | OHLC-exit без closed-флага |
| G1 | DECISION_MEMO vs exit_plan | ВАЖНО | План C не в бою |

---

*Аудитор не менял код и не открывал PR. Цифры сделок — снимok `models/paper_trades.jsonl` на момент клона master `0d56f0a`.*
