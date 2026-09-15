# External diagnosis: why ASTRA loses (Claude audit)

**Provenance**
- Author: external independent audit labeled «разбор Клода» (Claude)
- Date of diagnosis content: 2026-09-15
- Data basis (as stated by auditor): live paper week ~2026-09-08…15, codebase `entry_gates.py`, `pipeline.py`, `momentum.py`, strategy MFE/MAE
- Ingested into repo on branch `research/4h-onboarding-package`
- Status: **external opinion**, not owner decision. Live flags (entry_gates, htf_gate) are NOT changed by this research PR.

---

# Диагноз: почему ASTRA торгует в убыток

## Итоговая картина (факты из кода и данных)

| Метрика | Факт | Требуется | Статус |
|---|---|---|---|
| Win Rate (live) | **20.3%** | ≥55% | FAIL |
| Profitable days | **0 из 7** | ≥55% | FAIL |
| PnL (7 дней) | **−2167 USDT** | >0 | FAIL |
| PF (OOS audit) | **0.67** | ≥1.3 | FAIL |
| Avg trades/day | **28** (макс 68) | ~5–10 | WARN |
| Avg fee/trade | **2.73 USDT** | — | WARN |
| EV по стратегиям | **все отрицательные** кроме одной | >0 | FAIL |

## Причина №1 — Торговля в боковике без блокировки (главная)

70% сделок в `LOW_VOLATILITY` и `T:RANGE/V:VERY_LOW` (WR≈20%, EV≈−0.3R).
В коде `EntryGateConfig.enabled = False`, `shadow_enabled = True` — гейт только наблюдает.
HTF-фильтр тоже shadow.

## Причина №2 — Confidence ≠ p_win

При отсутствии ML `p_win = max(0.4, confidence)`; momentum поднимает confidence до 0.7–0.8 при реальном WR 20% → EV-гейт пропускает убыточные сделки.

## Причина №3 — min_rr = 1.5 при WR 20%

Безубыточность требует RR ≥ 4.0 при WR 20%. С RR 1.5 математическое ожидание ≈ −0.50R.

## Причина №4 — Оверtrading 28–68 сделок/день

При отрицательном EV больше сделок = быстрее слив. Fees ~76 USDT/день при 28 сделках.

## Причина №5 — Два типа проблемных стратегий (MFE/MAE)

### Группа А: цена идёт в нужную сторону, exit/fees убивают

| Стратегия | MFE (R) | Fee | Проблема |
|---|---|---|---|
| trend_following | 0.43 | 3.54 | fees > profit |
| cup_and_handle | 0.65 | 3.48 | TP слишком далеко |
| flag | 0.59 | 3.46 | exit слишком рано |
| rectangle | 0.57 | 2.34 | fees > profit |
| head_shoulders | 0.65 | 1.51 | единственная +EV в live (малая n) |

### Группа Б: цена сразу против (нет directional edge)

| Стратегия | MAE | MFE | Проблема |
|---|---|---|---|
| expanding_triangle | 1.10 | 0.33 | против тренда |
| zscore_mean_reversion | 0.72 | 0.15 | нет edge в крипте |
| liquidity_sweep | 0.73 | 0.37 | ложные пробои |
| ascending_triangle | 0.64 | 0.08 | нет валидации |

## Причина №6 — Слишком много стратегий с нулевой выборкой

~33 активные; meta с min_ev_samples=30 не учится; веса у prior из некалиброванного confidence.

## Рекомендации аудитора (НЕ применяются этим PR)

1. entry_gates_enabled=True, max_costs_r=0.25
2. htf_gate_enabled live
3. min_rr → 3.0 (после подъёма WR)
4. confidence base ↓ / брать WR из stats
5. max_trades_per_day 5–8
6. временно disable группа Б
7. фокус ts_momentum / head_shoulders
8. сузить universe до 3–5 символов

**Этот research PR не включает ни один из пунктов выше в live.**
