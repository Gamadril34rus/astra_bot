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

**0.7 был багом** (гейт и исполнитель говорили разное). **3.0** не согласован с `take_rr_min/max = 2.0–2.3`. **2.0** — рабочая точка: гейт и clamp про одно.

---

## Окна замера

| Окно | Назначение |
|------|------------|
| **13.09 → 23.09** | **Сохранить** отдельной секцией отчёта: «что дал спринт» — diff режимов по n, meanR, PF, max сделок/день. Не смешивать с новым режимом. |
| **23.09 → ~06–07.10** | Честный замер **нового** режима (лимит 6, guard, denylist, min_rr→2.0 с 24.09). Срез ~06–07.10. |

Инструмент: `scripts/measure_two_week_review.py` (read-only по `models/`).

---

## Процесс (навсегда)

1. **Правки живого контура** — только обычные PR.  
   **Запрещено:** one-shot workflows, `TRIGGER_*`, CI-хирургия восстановления файлов.
2. **Ритуал вотчины:** любое изменение живого контура = сообщение владельцу в **3 строки**  
   (что / почему / что наблюдаем) и его **«одобряю» ДО мержа**.

---

## Hypotheses (срез, не сброшены)

| ID | Hypothesis | Status | Why |
|----|------------|--------|-----|
| H-tsm45 | TSM45 multi-pair edge | **ОПРОВЕРГНУТА** | Panel 32 OOS PF 0.85; OOS-select shadow forbidden |
| H-family-A | trend/cup/flag/rectangle on 4h | **ОПРОВЕРГНУТА (proxy)** | costs 0.30% RT + RR≈2: PF 0.84–0.91 |
| H-family-B | zscore/sweep/expanding on 4h | **ОПРОВЕРГНУТА (proxy)** | PF 0.73–0.80 |
| H-breakout-4h | breakout+retest OOS | **шум с намёком** | shadow only; risk not raised |
| H-entry-gates | median(hypothetic_r)<0, n≥30 | **ждёт данных** | shadow ON, live OFF; tool `entry_gate_shadow_report.py` |
| H-track-C Z* | Zeus deep history | **ждёт / OOS fail for Z3–Z7** | не тащить в бой без нового OOS |
| H-cooldown-A2 / 5m | — | **только живые данные** | решение на срезе 06–07.10 |

---

## P3 — HTF-тень (живо, в срез 06–07.10)

| Элемент | Статус |
|---------|--------|
| `htf_shadow_enabled` | **True** (DecisionConfig) |
| `htf_gate_enabled` | **True** (мягкий контур) |
| `htf_hard_gate_enabled` | **False** на TradingEngineConfig — тень/выкл до накопления данных |
| Решение на срезе | вкл hard только если живые данные за 23.09–06.10 не режут edge в ноль |

Наблюдать: `models/htf_shadow_bans.jsonl`, доля контртрендовых отказов, PF до/после.

---

## P5 — пакет среза (живо, цель ~06–07.10)

1. Финальная синхронизация этого memo (не обнулять историю 13–23.09).  
2. Блок решений среза: entry_gates / cooldown-A2 / 5m-нога / 4h-ключи / zeus wedge тень / stats / риск.  
3. «Что наблюдать после среза» — короткий список метрик и файлов.  
4. Прогон `scripts/measure_two_week_review.py` → приложение к PR среза.

---

## Toggles (на срезе)

| Toggle | Рекомендация |
|--------|----------------|
| `entry_gates_enabled` | только если shadow PASS (median&lt;0, n≥30) |
| `htf_hard_gate_enabled` | только по живым данным P3 |
| `min_rr` | **2.0** (24.09) |
| Group B / tsm45 multi-pair | **OFF** |
| Stats reset / risk raise | **нет** |

---

## One-liner

> Режим 23.09: дисциплина (лимит 6, guard, denylist) + min_rr выровнен на 2.0 под clamp; замер нового режима с 23.09, срез ~06–07.10; окно 13–23.09 — отдельный diff «что дал спринт»; live только через PR + «одобряю».
