# Zeus diagnosis response — сверка Кими ↔ репо ↔ план

**Дата:** 2026-09-18  
**Статус:** research-only. Live-контур **не** меняется до среза 26–27.09.  
**Источники:** 9 транскриптов Зевса, разбор Кими, `docs/TAKE_RR_FEASIBILITY.md`,
`docs/HTF_DIRECTIONAL_FILTER_PLAN.md`, `docs/research/DECISION_MEMO_2026-09-26.md`,
`config/settings.yaml`, paper/audit-отчёты.

---

## 1. Диагноз Кими — что подтверждено репо

| Утверждение Кими | Факт в astra | Вердикт |
|---|---|---|
| Конфликт стратегий (momentum ↔ mean_reversion и др.) | `settings.yaml`: momentum, mean_reversion, book_breakout, ts_momentum, ts_momentum_adx, multicurrency_mtf — все `enabled: true` | **Подтверждено** |
| Overtrading / PF < 1 | Multicurrency audit PF ~0.79 OOS ~0.67; paper realized −R | **Подтверждено** |
| Тейки 2R+ недостижимы | `TAKE_RR_FEASIBILITY`: 123 сделки, MFE max 1.59R, **0/123** ≥ 1.8R; mean R = −0.33 | **Подтверждено** |
| Нет обязательного HTF на входе 5m | План D5; pipeline primary = 5m; не-флип стратегии без 4h-согласия | **Подтверждено** (см. §3) |
| Стоп/вход не от структуры | ATR-стопы, входы «в середине» → много SL | **Согласуется** |
| Kelly/ML не лечит PF<1 | ML `enabled: false`; сайзинг не создаёт edge | **Подтверждено** |
| «Одна торговая идея» | Зевс урок 2–3: старший ТФ → зона → ретест | **Принято как цель** |

---

## 2. Где Кими заостряет — и наш ответ

1. **«Выключить всё кроме одной логики завтра»**  
   По смыслу верно, по процессу — нет. До среза: shadow / research / fail-closed.  
   Резкий выруб live без замера = смена эксперимента без контроля.

2. **RR ≥ 2 как жёсткий гейт на текущем 5m-потоке**  
   На median MFE 0.28R это «тейка нет». Сначала вход **от зоны** (стоп близко),  
   потом RR≥1.5–2 становится физически достижимым. См. `RECOMMENDED_CONFIG.md`
   (OOS breakout+retest 4h: take 1.5–2.0R, не 2.5+).

3. **zeus_wedge_retest_4h**  
   Это **один** паттерн урока 8 (ложный выход клина), не вся система Зевса.  
   Иерархия: риск → фаза → зона → канал/клин → FVG → индикаторы-фильтры  
   (`ZEUS_METHOD_MAP.md`). Клин = слой #7.

4. **Дивергенция RSI**  
   Согласны: как **вето**, не как отдельная стратегия (`RSI_DIVERGENCE_RESEARCH`).

---

## 3. HTF shadow (D5 фаза 0) — уже в коде

Реализовано (не этот PR):

- `astra_bot/decision/htf_shadow.py` — `htf_bias` (EMA20/50 на закрытых 4h)
- `DecisionPipeline._htf_shadow_observe` — пишет гипотетические запреты
- `DecisionConfig.htf_shadow_enabled = True` (по умолчанию)
- Журнал: `models/htf_shadow_bans.jsonl`
- Тесты: `tests/unit/test_pipeline_htf_shadow.py`

**Свойства фазы 0:**
- входы **не** блокирует;
- флипы (`ts_momentum`, `ts_momentum_cross`) исключены;
- fail-open при < 60 закрытых 4h-баров;
- после n≥300 — замер «запрещённые бы» vs факт R → решение о фазе 1.

Живой blocking HTF-gate (`htf_gate_*` на 1d) — отдельный контур владельца;
включение только на срезе по данным.

---

## 4. Paper «одна идея» — черновик (не prod)

Файл: `config/paper_one_idea.yaml`

**Не** подключается к `settings.yaml` автоматически.  
Назначение: paper/sim после среза — одна структурная линия + MTF-контекст,
без mean_reversion как контртрендового конкурента.

Принцип:
- mean_reversion → off
- дубли ts_momentum / ts_momentum_adx → одна обвязка
- multicurrency_mtf → off или weight↓ до починки BTC
- 4h research-ключи (`zeus_wedge_retest_4h`, breakout-family) → enabled=false, shadow
- risk_per_trade ≤ 0.4–0.5%

---

## 5. Сводный алгоритм (Зевс → astra, цель после среза)

```
1. Контекст D1/W1: фаза (тренд/флэт), не против старшего
2. Зона 4H: уровень / ОБ / брейкер / граница клина
3. Подтверждение 1H–15m: ретест + реакция + объём
4. Вход от зоны; стоп за зоной/экстремумом
5. TP ≥ 1.5R (при структурном стопе); после +1R → BE
6. Вето: RSI-див 4h против лонга; выходные/пт вечер — без новых
7. Сайзинг 0.4–1% из стопа; Kelly только при PF>1.1 и n≥200 (IS+OOS)
```

---

## 6. Приоритеты до / на срезе 26–27.09

| # | Действие | Статус |
|---|---|---|
| 1 | Live weights / kill-switch **не** ослаблять | действует |
| 2 | HTF shadow копит `htf_shadow_bans.jsonl` | **уже on** |
| 3 | `zeus_wedge_retest_4h` research, enabled=false | в master |
| 4 | Paper one-idea yaml — черновик | **этот PR** |
| 5 | entry_gates enable только если shadow PASS (median R<0, n≥30) | memo |
| 6 | Не поднимать 5m / не смешивать статы с 4h | memo |
| 7 | tsm45 multi-pair — закрыт как общий edge | memo |

---

## 7. One-liner

> Кими прав по диагнозу: конфликт идей, overtrading, недостижимые тейки, нет HTF на 5m.  
> Ответ astra: не «вырубить всё завтра», а shadow → одна идея на paper → promote  
> только по pre-registered критериям; HTF-shadow уже пишет; live до среза не трогаем.
