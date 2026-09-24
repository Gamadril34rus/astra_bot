# Decision memo — owner slice window (shifted)

**One document = one truth.** Live contour changes only via normal PRs + owner «одобряю».

---

## Режим изменён 23.09.2026 (Sprint «Зевс-Активация», утверждён задним числом)

Смена режима (не «багфикс в вакууме»):

| Параметр | Было (до 23.09) | Стало 23.09 | Коррекция 24.09 |
|----------|-----------------|-------------|-----------------|
| `min_rr` | **0.7** (баг: тейк clamp 2.0–2.3, гейт 0.7) | 3.0 | **2.0** — согласовано с `clamp_take_rr` |
| `max_trades_per_day` | нет лимита | **6** | без изменений |
| `SymbolLossGuard` | нет | **3 убытка → 4h** по символу | без изменений |
| Group-B denylist | частично | zscore / liquidity_sweep / fvg / oi / triangles / breakout | без изменений |
| `entry_gates_enabled` | false (тень) | **false** (тень до критерия) | без изменений |
| риск / stats | — | **не поднимать / не обнулять** | без изменений |

**0.7 был багом.** **3.0** не согласован с `take_rr_min/max = 2.0–2.3`. **2.0** — рабочая точка.

**Запрет:** третья смена режима в том же месяце обнуляет выводы замера — до среза правила окна 23.09 не крутить.

---

## Окна замера

| Окно | Назначение |
|------|------------|
| **13.09 → 23.09** | Отдельная секция: «что дал спринт» (n, meanR, PF, max сделок/день). Не смешивать с новым режимом. |
| **23.09 → ~06–07.10** | Честный замер **нового** режима (лимит 6, guard, denylist, min_rr=2.0 с 24.09). |

**Правило n (зафиксировано 24.09 заранее):** если к **26–27.09** закрытых сделок режима 23.09 всё ещё **n<10** — окно **продлевается до ~06–07.10 без смены правил**. Не ускорять n третьим ретюном.

Инструмент: `scripts/measure_two_week_review.py` (read-only по `models/`).

На 24.09: post-sprint paper **n=0**; бот жив (NO_TRADE 23–24.09 n≈5031: no_signal / LOW_EV / rr_too_low / liquidity) — фильтры, не тихая поломка.

---

## Геометрия тейка (research 24.09, в бой не включено)

Источник: `reports/tp_geometry_mfe_2026-09-24.md`.

| Ранг | План | Комментарий |
|------|------|-------------|
| **PRIMARY** | **C — один тейк 0.70R** | Устойчив к path-order и к усечению MFE текущими выходами |
| **BACKUP** | A — 50% @ 0.70R + 50% @ 1.40R | Хвост 1.40R недооценён truncation bias |
| DROP | B — нога 0.28R | На уровне круглых издержек, не робастна |
| baseline | CURRENT clamp 2.0–2.3 | MFE≥1.8R ≈ 2.5% сделок — недостижим как массовая цель |

**Настоящий кандидат среза = БАНДЛ:** `entry_gates` ON (только если PASS + arrival) **+ план C**. По отдельности не лечат.

---

## Воронка и arrival rate (entry_gates) — зафиксировано 24.09

`entry_gates` в конце конвейера. 23–24.09: ARRIVAL на стадию entry_gate **≈ 0 / сутки**. Тень голодает от пустого притока.

**Правило судьбы:** к срезу 06–07.10 если n<30 **и** arrival < 1/сут → «гейт избыточен при текущих фильтрах; тень как страховка» — не ждать бесконечно. 30.09 — повтор (n, median, arrival/сут).

---

## Zeus P1 — приёмка цикла (24.09)

`zeus_trade_journal.jsonl`: entry=47, stop_adjust=21, exit=15; 3 open paper. P1 закрыт.

---

## ZEUS vs MAIN (collect, no judgment until slice)

Template report: `reports/zeus_vs_main_2026-09-24.md`.

| Metric (24.09 snapshot) | MAIN | ZEUS |
|-------------------------|-----:|-----:|
| n closed | 240 | 18 |
| meanR net | −0.27 | −0.21 |
| PF | 0.28 | 0.59 |
| hold median (h) | 0.35 | 3.9 |
| costs_r median | 0.20 | 0.33 |

Zeus hygiene: raw entry 47 → **unique ~23** (5‑min collapse); stop_adjust=21; realized_pnl = sum(closed pnl). Recompute at slice with larger n.

---

## Процесс (навсегда)

1. Правки живого контура — только обычные PR.  
2. Ритуал: 3 строки + **«одобряю» ДО мержа**.

---

## Hypotheses (срез, не сброшены)

| ID | Status |
|----|--------|
| H-entry-gates | ждёт / риск голода (arrival≈0); судьба по arrival на срезе |
| H-tp-geometry | research+ C 0.70R |
| H-track-C Z* | ждёт |

---

## P3 / P5 / Toggles / Calendar

- P3 HTF: ~5000 bans, hard=False; на срезе сверка + scalp5m  
- P5: memo + measure_two_week_review + Zeus vs Main  
- 26–27.09: n<10 → extend window  
- 30.09: gates shadow  
- 06–07.10: slice  
- Live: не трогать до среза  

## One-liner

> Режим 23.09 держим; TP=C (+A) в бандле с gates только если тень+arrival; иначе gates=страховка; Zeus P1 OK; Zeus vs Main — collect до среза; live только PR + «одобряю».
