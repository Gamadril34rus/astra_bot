# Unified ExitPlan — карта текущего состояния (бэклог B8, этап 1: проект)

> Статус: ПРОЕКТ для одобрения владельцем. Продакшен-код в этом PR не
> меняется. Реализация — отдельным PR после одобрения (этап 2).
> Все ссылки — master на 10.09.2026 (после мержа PR #69).

## 1. Зачем это всё

Сейчас судьбу открытой позиции решают СЕМЬ независимых мест кода. Каждое
по-своему правильное, но:

- одна и та же логика (трейлинг, безубыток, выход по режиму) написана
  в 2–3 экземплярах с разными параметрами;
- итоговый результат зависит от ПОРЯДКА вызовов, а порядок зашит в
  `process_symbol`;
- бэктестер видит только стоп/тейк — то есть гипотезы выходов
  (Exit Research, TZ §16/§17) нельзя честно проверить на истории.

Предложение в конце (§5): один объект «план позиции», который выбирается
на ВХОДЕ, дальше — только исполнение по явным правилам.

## 2. Карта: кто и что решает по позиции

Цикл по символу — `astra_bot/decision/trading_engine.py:1019-1096`
(`process_symbol`). Порядок вызовов (нумерация — как в коде):

### Слой P. BTC PANIC — упреждающий флатт (до всего остального)

`trading_engine.py:1031-1037`:

```python
        # BTC PANIC (Этап 4): это ВЫХОД, а не вход — работает независимо
        # от risk-состояния (HALT не отменяет обязательные выходы).
        if self.exit_manager.btc_panic:
            panic = self.exit_manager.flatten_symbol(
                symbol, float(ctx.current_price)
            )
            if panic:
                self._record_closed(panic)
            return panic
```

Флаг ставится раз в цикл из 4h-свечей BTC: `exit_manager.py:83-115`
(`refresh_btc_panic`, вызов — `trading_engine.py:1703`). Закрытие ВСЕХ
позиций символа по живой цене с причиной `BTC_PANIC`
(`exit_manager.py:160-165`).

Особенность: этот слой РАНЬШЕ всего и не проходит ни через контроллер,
ни через брокерные стопы — причина всегда `BTC_PANIC`.

### Слой E. Exit Controller — «умный дефолт» и гипотезы выходов

Вызов: `trading_engine.py:1069-1073` — ДО брокерных стопов (по TZ §16
стопы корректируются до их проверки на том же баре):

```python
        forced: list = []
        try:
            forced = self.exit_controller.apply(
                self.broker, symbol, last_bar, list(primary), regime_name
            )
```

Выбор плана: активная гипотеза выхода → её вариант; иначе дефолт
(`exit_controller.py:93-112`, `plan_for`). Варианты перечислены в
шапке (`exit_controller.py:16-25`): STATIC_TP / ATR_STOP /
STRUCTURE_STOP / TRAILING / BREAKEVEN / TIME_STOP / MOMENTUM_EXIT /
REGIME_EXIT.

Пока гипотез нет, работает SMART_DEFAULT_PLAN
(`exit_controller.py:47-53`):

```python
SMART_DEFAULT_PLAN: list[tuple[str, dict[str, Any]]] = [
    ("BREAKEVEN", {"trigger_r": 0.8, "net": True}),
    ("TRAILING", {"k": 2.0}),
    ("MAE_CUT", {"mae_r": 0.9}),
    ("REGIME_EXIT", {"exit_regimes": ["PANIC"]}),
]
```

что даёт (`exit_controller.py:153-224`, `_apply_smart`):

- **MAE_CUT** — цена против нас на 0.9R (с плечом порог ×0.75) →
  закрытие в close (`:177-190`):

```python
        mae_r = 0.9 * scale
        if risk > 0:
            r_now = (close - entry) / risk if pos.direction == "long" \
                else (entry - close) / risk
            if r_now <= -mae_r:
                trade = broker.close_position(pos.id, _dec(close), "mae_cut")
```

- **REGIME_EXIT** — режим развернулся против позиции → закрытие в close
  (`:191-202`, список `_ADVERSE_REGIMES` `:56-59`):

```python
        adverse = _ADVERSE_REGIMES.get(pos.direction, set())
        if current_regime in adverse and current_regime != (pos.regime or ""):
            trade = broker.close_position(pos.id, _dec(close), "regime_exit")
```

- **нетто-BREAKEVEN** — после 0.8R в плюс стоп в НЕТТО-безубыток
  (вход+комиссии+фандинг, `broker.net_breakeven_price`) (`:204-222`):

```python
            if mfe_r >= trigger_r:
                target = (
                    broker.net_breakeven_price(pos)
                    if hasattr(broker, "net_breakeven_price")
                    else _dec(entry)
                )
```

- **трейлинг k*ATR** — только подтягивание (`:225-243`, `_smart_trail`):

```python
        if pos.direction == "long":
            anchor = pos.highest_price if pos.highest_price is not None else bars[-1].high
            new_stop = float(anchor) - k * atr
            if new_stop > float(pos.stop_loss):
                pos.stop_loss = _dec(new_stop)
```

Для позиций С активной гипотезой — варианты через `_adjust_stop`
(`:270-333`) и вынужденные закрытия через `_forced_close_hit`
(`:245-267`: TIME_STOP по барам, MOMENTUM_EXIT по EMA9, REGIME_EXIT по
списку режимов из параметров гипотезы).

### Слой B. Брокер — стопы, частичные тейки, «свой» трейлинг

`broker.check_exits` (`astra_bot/decision/broker.py:472-556`), вызов
`trading_engine.py:1074`:

```python
        closed = forced + self.broker.check_exits(last_bar)
```

- **стоп-лосс** по теням бара (`:480-489`):

```python
            stop_hit = (
                (pos.direction == "long" and low <= pos.stop_loss)
                or (pos.direction == "short" and high >= pos.stop_loss)
            )
            if stop_hit:
                closed.append(self._close(pos, pos.stop_loss, "stop_loss"))
```

- **частичные тейки** tp1/tp2/tp3 с агрегацией H (причины `tp1`, `tp2`…,
  `:490-533`);
- после tp1 — свой безубыток и включение трейлинга (`:534-541`):

```python
                if i == 0:
                    pos.trailing_activated = True
                    be = self.net_breakeven_price(pos)
                    if pos.direction == "long":
                        pos.stop_loss = max(pos.stop_loss, be)
```

- после tp2 — свой трейлинг на расстоянии |entry − tp1|
  (`:542-547`):

```python
                if i == 1 and pos.direction == "long" and pos.highest_price:
                    pos.stop_loss = max(
                        pos.stop_loss, pos.highest_price - abs(pos.entry_price - pos.take_profits[0])
                    )
```

### Слой L. Ликвидации (плечо > 1, по mark price)

Вызов: `trading_engine.py:1077-1080` (ПОСЛЕ брокерных стопов — стоп по
замыслу срабатывает раньше):

```python
            await self._sync_perps_state(symbol, last_bar.close)
            liq_closed = self.broker.check_liquidations(symbol)
            closed = closed + liq_closed
```

Механика: `broker.py:755-791` — mark пробил `liquidation_price` →
закрытие по цене ликвидации с причиной `liquidation`.

### Слой S. Exit Manager — обязательные safety-выходы (legacy-поток)

Вызов ПОСЛЕДНИМ: `trading_engine.py:1087-1093`:

```python
        try:
            open_pos = [p for p in self.broker.positions if p.symbol == symbol]
            if open_pos:
                closed = closed + self.exit_manager.check_symbol(
                    open_pos, ctx.candles, float(ctx.current_price)
                )
```

`exit_manager.check_symbol` (`exit_manager.py:118-158`):

- **MAX_HOLD** — 48 часов удержания (`:135-137`, параметр `:37`):

```python
            if pos.opened_at and (now - int(pos.opened_at)) >= cfg.max_hold_seconds * 1000:
                reason = "MAX_HOLD"
```

- **VOL_EXPANSION** — True Range текущего бара ≥ 2× медианы окна →
  выход по живой цене (`:138-149`, параметры `:38-40`):

```python
                        med = statistics.median(series[:-1])
                        last = series[-1]
                        if med > 0 and last >= cfg.vol_expansion_ratio * med:
                            reason = "VOL_EXPANSION"
```

Шапка файла честно называет его роль: «обязательный safety-контур,
активный ВСЕГДА, независимо от гипотезы выхода» (`exit_manager.py:1-16`).

### Слой D. Решение пайплайна — CLOSE и FLIP

После выходов, при обработке решения по символу
(`trading_engine.py:1122-1140`):

```python
        # CLOSE: режим тренда окончился — закрываем позицию без входа.
        if decision.action == "CLOSE":
            newly = self.broker.close_positions(symbol, ctx.current_price, "flat_regime")
```

```python
        # FLIP: переворот — закрываем противоположную позицию и входим заново.
        if decision.action == "FLIP" and has_position:
            newly = self.broker.close_positions(symbol, ctx.current_price, "flip")
```

### Слой T. Структурный стоп/тейк — на ВХОДЕ, не во время жизни

До сайзинга (`trading_engine.py:1204-1238`): стоп выносится за свинг по
теням (`exit_controller.structural_stop`, `:341-384`), тейк двигается
вслед с сохранением RR. Причины в логе — `STRUCT-STOP`/`STRUCT-TP`.
На открытую позицию НЕ влияет — работает только в момент входа.

### Что НЕ закрывает позиции

Kill-switch по статистике (`trading_engine.py:646-695`) только убирает
стратегию из пайплайна (блокирует новые входы), открытые позиции не
трогает. `market_safety.check` (`trading_engine.py:1174-1189`) — тоже
только вход.

## 3. Где логика задваивается (та же идея в 2–3 местах)

| # | Логика | Экземпляры | Различия |
|---|--------|-----------|----------|
| 1 | Трейлинг | `exit_controller._smart_trail` (`:225-243`, k×ATR, смарт-дефолт) · `exit_controller._adjust_stop TRAILING` (`:303-317`, k×ATR, вариант гипотезы) · `broker.check_exits` после tp2 (`:542-547`, дистанция до tp1) | три разных якоря и шага; какой работает — зависит от того, был ли tp1 и есть ли гипотеза |
| 2 | Безубыток | `exit_controller._apply_smart` (`:204-222`, нетто, триггер 0.8R) · `broker.check_exits` после tp1 (`:534-541`, нетто, триггер = факт tp1) · `_adjust_stop BREAKEVEN` (`:325-333`, СЫРОЙ вход, не нетто) | два нетто + один сырой; у гипотезного варианта безубыток ХУЖЕ (не учитывает издержки) |
| 3 | Выход по режиму | `_apply_smart` (`:191-202`, жёсткий список `_ADVERSE_REGIMES`) · `_forced_close_hit REGIME_EXIT` (`:262-266`, список из параметров гипотезы) | одинаковый смысл, два источника правил |
| 4 | «Риск-конверт» | MAE_CUT в контроллере (закрытие в close при −0.9R) · стоп брокера (закрытие по стоп-цене при −1R) | слоение осознанное («отторговка»), но порог и цена закрытия живут в разных файлах |
| 5 | Реакция на волатильность | VOL_EXPANSION менеджера (TR против медианы) · трейлинг контроллера (ATR) | оба «режутся на спайке», триггеры не согласованы |

## 4. Где решения пересекаются/конфликтуют (порядок в process_symbol)

Порядок `process_symbol` (`trading_engine.py:1019-1096`): PANIC →
extremes → контроллер → брокер-стопы → ликвидации → менеджер. Следствия:

1. **Атрибуция причины зависит от порядка.** Позиция с MAX_HOLD 48ч,
   у которой на том же баре выбило стоп, закроется как `stop_loss`
   (слой B раньше слоя S) — статистика выходов (`strategy_stats.json`,
   уроки) запишет «стоп», хотя решал MAX_HOLD. Аналогично VOL_EXPANSION
   молчит, если контроллер уже резанул MAE_CUT.
2. **Два автора одного стопа на одном баре.** Контроллер подтянул стоп
   (трейлинг/безубыток) — брокер тут же проверяет ЕГО ЖЕ на том же баре
   (`trading_engine.py:1069-1074`). Это осознанно (TZ §16), но означает,
   что контроллер фактически исполняет выходы рукам брокера, и его
   «трейлинг» — не удержание, а немедленная угроза закрытия.
3. **PANIC в обход всего** (слой P) — единственный выход, который не
   проходит через контроллер; его нет ни в одной статистике планов.
4. **Бэктестер не знает слоёв.** В бэктестере только стоп/тейк по бару
   (`backtester/engine.py:665-702`, `_check_exit_conditions`) и флаты
   `flat_regime`/`flip`/`end_of_backtest`. Ни MAE_CUT, ни REGIME_EXIT,
   ни MAX_HOLD, ни VOL_EXPANSION, ни частичных тейков, ни ликвидаций.
   Значит: гипотезу выхода, «подтверждённую» бэктестом, live исполняет
   СОВСЕМ другими правилами — Exit Research (TZ §16/§17) сейчас не имеет
   честного полигона.

## 5. Предложенный план: Unified ExitPlan (на одобрение)

Принцип: **план выбирается на ВХОДЕ** (гипотеза или смарт-дефолт),
фиксируется в позиции, дальше — только ИСПОЛНЕНИЕ и пересчёт по явным
правилам. Одно место отвечает на вопрос «что происходит с позицией».

```text
Вход:  ExitPlan = план_из_гипотезы(strategy, regime) ИЛИ SMART_DEFAULT
       plan закрепляется в позиции (id плана + параметры + версия правил)
Бар:   один проход исполнителя в ФИКСИРОВАННОМ порядке приоритета:
         1. liquidation (биржевое событие, не отменить)
         2. stop (уровень из плана; исполнение — брокер)
         3. take-лестница (tp1..tpN из плана)
         4. правила плана в close: MAE_CUT / TIME_STOP / MOMENTUM_EXIT /
            REGIME_EXIT / VOL_EXPANSION — все как ПРАВИЛА ОДНОГО плана
         5. пересчёт уровня стопа: breakeven / trailing (ОДНА реализация)
       каждое срабатывание пишется с id плана и номером правила
Брокер: только исполнитель уровней (стоп/тейк/ликвидация) — как биржа
```

Этапы (каждый — отдельный PR, поведение меняется ступенчато):

- **Этап A (не меняет поведение).** `ExitPlan` как объект + закрепление
  за позицией + ПОЛНЫЙ журнал решений (какое правило, какой план,
  какой слой СЕЙЧАС сработал бы первым). Сравнение журнала с текущим
  поведением на paper — доказательство эквивалентности до любых
  переключений.
- **Этап B (выравнивание дублей).** Один трейлинг, один безубыток, один
  regime-exit: правила смарт-дефолта и гипотез становятся правилами
  одного плана; `check_exits` брокера перестаёт двигать стопы сам
  (после tp — через правила плана). VOL_EXPANSION/MAX_HOLD менеджера
  переезжают в план как обязательные правила-дефолты (safety-слой
  сохраняется, но виден в журнале).
- **Этап C (полигон для бэктеста).** Исполнитель планов становится
  чистой функцией (бар × план × позиция → решения), подключается в
  бэктестер — гипотезы выходов начинают проверяться на истории в тех же
  правилах, что и live.

Риски/вопросы к владельцу:

1. Этап B меняет доли причин выходов (часть `stop_loss` станет
   `mae_cut`/`trail_stop` и т.п.) — это затронет `strategy_stats.json`
   и сравнение «до/после». Приемлемо?
2. Порядок правил в приоритете (§5, шаги 1–5) — согласен ли владелец
   с тем, что ликвидация и биржевой стоп всегда старше правил плана?
3. Этап C затрагивает бэктестер (engine.py) — его семантика бара
   описана в docs/BACKTEST_VS_PAPER_SEMANTICS.md; правила планов в
   бэктесте считаем по закрытым барам (та же семантика, что сейчас).

## 6. Что наблюдать после мержа (этого PR)

- Ничего в поведении не меняется — PR документальный.
- К точке одобрения этапа A: согласовать формат журнала решений
  (куда пишем: jsonl в models/ под Save-state или лог) — вопрос будет
  задан отдельно в PR этапа A.

---

## 7. Почему tp1 почти не срабатывал (1-2 на 80+ сделок) — ответ карты плана

Решение владельца №1 (11.09) требует объяснения. Три причины, все — по коду и state:

**Причина 1: тейк стратегии на входе ВЫБРАСЫВАЛСЯ, ставилась лестница 1R/1.8R/2.5R.**

`astra_bot/decision/broker.py:397-419` (до B8.2):

```python
        # Разбиваем тейк на 3 уровня: 1R, 1.8R, 2.5R.
        # Флип-стратегии (ts_momentum) живут до смены режима — тейки им
        # не нужны, иначе частичные выходы искажают проверенное правило.
        if no_take_profit:
            tps: list[Decimal] = []
        else:
            risk = abs(entry_price - stop_loss)
            if direction == "long":
                tps = [
                    entry_price + risk * Decimal("1.0"),
                    entry_price + risk * Decimal("1.8"),
                    entry_price + risk * Decimal("2.5"),
                ]
```

Параметр `take_profit` (ТП стратегии, прошедший гейт RR) только
валидируется (`:386-393`), в уровни не попадает. tp1 = 1R.

**Причина 2: цена почти никогда не доходит до +1R.** По 123 закрытым
сделкам 08–11.09 (`scripts/measure_rr_reach.py`): медианный MFE —
0.28R, максимальный — 1.59R, MFE ≥ 1R — у 4.9% сделок (6/123), ≥ 1.8R —
ни у одной. Медианное удержание — 25 минут.

**Причина 3: до +1R позицию срезают свои же правила.** Распределение
exit_reason (n=123): stop_loss 92 (75%), VOL_EXPANSION 19 (15%),
mae_cut 8 (7%), tp1 2 (1.6%), regime_exit 2. Контур «ранней отторговки»
(MAE_CUT −0.9R, VOL_EXPANSION по спайку) убирает позицию раньше, чем
та успевает сходить +1R — в текущем режиме рынка это 96% всех выходов.

Итог: tp1 не «почти не срабатывал» — он не мог срабатывать: до уровня
1R цена доходила в 5% случаев, и в половине из них позиция уже была
закрыта стоп-правилами. Решение владельца (один тейк 2-2.3R) закономерно
делает тейк редким событием по построению — его вклад появится только
вместе с более длинными удержаниями (см. docs/TAKE_RR_FEASIBILITY.md).

## 8. Этап 2 РЕАЛИЗОВАН (этот PR): единый исполнитель

Решения владельца (11.09) внедрены:

1. **Один тейк 2-2.3R** (ответ №1): `exit_plan.clamp_take_rr` — RR тейка
   стратегии зажимается в зону [2.0, 2.3]; уровень один, объём полный
   (`take_levels` в broker.open_position, частичные фиксации на новых
   входах исчезли). Флип-стратегии без тейка — как прежде.
2. **Жёсткая иерархия** (ответ №2): жёсткий стоп и тейк старше плана —
   STOP-правила плана применяются до проверки уровней (TZ §16), но
   FORCED-правила — только после (пережившие уровни позиции). План не
   может расширить или снять стоп: инвариант в `exit_plan.apply_tighter`
   (long — только вверх, short — только вниз). Ликвидация в paper —
   после стопов по mark, как и было (порядок process_symbol сохранён).
3. **Полигон в бэктестере** (ответ №3): чистые правила в
   `astra_bot/decision/exit_plan.py` используются обоими контурами.
   Paper — живые семантики (правила по close живого бара, safety по
   живой цене); бэктестер — закрытые бары (стоп-правила по данным
   СТРОГО до текущей свечи, forced — по её close; флаг
   `BacktestConfig.exit_plan_enabled`).

Файлы: `decision/exit_plan.py` (новый), `decision/trading_engine.py`
(init plan_engine; entry take_levels+attach; process_symbol rewiring),
`decision/broker.py` (take_levels + поля плана PaperPosition),
`backtester/engine.py` (_attach_plan/_plan_adjust_stops/_plan_forced_rules),
`core/state_store.py` (сериализация plan_take),
`engines/position_sizer.py` (D2: жёсткий потолок риска 3%),
`scripts/measure_rr_reach.py` (D4), тесты `tests/test_exit_plan_executor.py`.

Что где теперь решает:

| Слой | Было (7 мест) | Стало |
|---|---|---|
| BTC PANIC flatten | exit_manager | без изменений (PANIC — до всего) |
| STOP-правила | exit_controller.apply (смарт) + broker после tp | plan_engine.adjust_stops: D1-структурный (новый, по закрытым барам ТФ позиции) + нетто-БУ 0.8R + трейлинг 2×ATR + гипотезные варианты; ТОЛЬКО подтягивание |
| Уровни (стоп/тейк) | broker.check_exits | broker.check_exits (не менялся; на новых входах тейк один) |
| Ликвидации | broker.check_liquidations | без изменений |
| FORCED-правила | exit_controller.apply + exit_manager.check_symbol | plan_engine.forced_closes: MAE_CUT → time_stop → momentum_exit → regime_exit → MAX_HOLD → VOL_EXPANSION (safety — параметры ExitManagerConfig) |
| Структурный стоп на входе | structural_stop | без изменений (затем тейк клэмпится к RR плана) |

Ограничения полигона (задокументировано): в бэктестере недоступны
REGIME_EXIT (нет режимного движка), TIME_STOP/MOMENTUM_EXIT (гипотезные;
в бэктесте гипотез нет) — см. §6 плана этапа C. Старые классы
ExitController/ExitManager сохранены: plan_for (выбор гипотезы),
structural_stop (вход), refresh_btc_panic/flatten (PANIC).

Наблюдать после мержа: доли exit_reason у новых сделок (часть stop_loss
заменится mae_cut/структурным стопом — решение владельца №1 принимает
смену атрибуции), время удержания, вклад тейка (docs/TAKE_RR_FEASIBILITY.md).
