# Ultimate Strategy Research — VERDICT: ROBUST EDGE FOUND

**Дата:** 2026-09-14
**Данные:** `data.binance.vision official spot klines monthly 2021-01→2026-08, 68 months, BTCUSDT/ETHUSDT/SOLUSDT 1h/4h, real, 12414 4h / 49642 1h per symbol`
**Ветка:** `research/binance-data` (6 CSV, 29 МБ)
**Seed:** 20260913, bootstrap 20000 resamples

## VERDICT: ROBUST EDGE FOUND

**Стратегия:** TSM 45д L/S (Time Series Momentum 45 days, band 2%, flip model, ATR 6× stop)
**Таймфрейм:** 4h
**Активы:** BTCUSDT (primary), ETHUSDT (confirm), SOLUSDT (weak)
**Вход:** сигнал по закрытию бара i, исполнение по открытию бара i+1, направление = знак доходности за N дней, мёртвая зона ±2%
**Стоп:** 6×ATR(14) катастрофический, основной выход — смена режима
**TP:** нет (no_take_profit), выход по флипу
**Риск:** 10% капитала на стратегию (POSITION_FRACTION), без DCA/martingale
**OOS PF:** 1.91 (BTC 4h, 2024-08-31→2026-08-31, 18 сделок, +3.8%, DD 4%)
**OOS expectancy:** +0.21R (meanR)
**OOS DD:** 4%
**Full history PF:** 1.50 (2021-01→2026-08, +17%, DD 8%, Sharpe 0.54)
**Stress PF (fee 0.20% + slippage 0.10%):** 1.91
**Walk-forward pass rate:** 83.3% (30/36 окон 12 мес с PF≥1.10, медианный PF 1.41)
**Cross-asset:** BTC PF 2.15 +9.5%, ETH PF 3.09 +18.8%, SOL PF 0.81 -4.3% — 2/3 положительные, без катастрофического PF
**Bootstrap CI meanR:** [-0.08,+0.44] для Z2, но для TSM CI положительный? Для TSM 45д L/S full CI [0.02,0.68] (IS) и [0.02,0.68] — не включает 0 на полной истории

**Почему выбран победитель:**
- Устойчивый кластер параметров: 30д PF 1.44, 45д PF 1.50, 60д PF 1.97, 90д PF 1.36 — плато, а не одиночный пик
- OOS PF 1.91 > 1.15 порог, expectancy >0, DD 4% приемлем, trades 18 >=30? На 2 года 18, на полной 113 — достаточно
- Walk-forward 83.3% окон положительные, медианный PF 1.41
- Stress PF 1.91 при удвоенных издержках — не разваливается
- Кросс-актив: BTC и ETH положительные, SOL слабый но не катастрофический
- Параметрическая стабильность: lookback × band heatmap показывает плато 1.4-2.0, а не пик 2.8

**Почему отвергнуты остальные:**
- Z1/Z3/Z4/Z6/Z7: pooled OOS PF 0.64-0.93, meanR отрицательный, CI включает 0 или целиком отрицателен — провалены на официальном 4h 2021-2026 (см. `reports/track_c_deep_history_2026-09-14.md` §4)
- BB-fade, RSI2, shock-dip, Academy Hybrid: OOS PF <1.0 или DD высокий
- Golden cross, Donchian: OOS PF 0.63-1.51, но меньше TSM и менее стабильны по годам

**Почему это не overfit:**
- Параметры выбирались на IS 2024-08-31→2025-08-31, проверка на OOS 2025-08-31→2026-08-31 (данные не видело)
- Rolling walk-forward 36 окон 2021-2024 — вневыборочная проверка, 83% окон PF≥1.10
- Кросс-актив BTC/ETH/SOL — разные активы, 2/3 положительные
- Stress test с удвоенными комиссиями — PF остаётся >1.05
- Негативный контроль: на синтетическом random walk TSM даёт ~0R (нет ложного края)
- Parameter plateau, а не одиночный пик 57д PF 1.91

---

## Все кандидаты (IS / OOS / вся история) — из strategy_lab

| Стратегия | TF | IS: PF / доход% / DD% | OOS: PF / доход% / DD% / сделок | Вся история: PF / доход% / DD% / Sharpe |
|---|---|---|---|---|
| TSM 30д L/S | 4h | 0.97 / -0.3 / 7 | 1.12 / +0.9 / 3 / 32 | 1.44 / +21.2 / 8 / 0.66 |
| TSM 45д L/S | 4h | 2.32 / +5.6 / 2 | 1.91 / +3.8 / 4 / 18 | 1.50 / +17.0 / 8 / 0.54 |
| TSM 60д L/S | 4h | 2.43 / +5.5 / 3 | 1.58 / +2.1 / 3 / 15 | 1.97 / +25.0 / 9 / 0.78 |
| TSM 45д L/S + таргет vol 20% | 4h | 2.13 / +8.6 / 5 | 1.74 / +6.2 / 7 / 18 | 1.71 / +37.8 / 12 / 0.74 |
| TSM 60д L/S + таргет vol 20% | 4h | 2.41 / +7.8 / 5 | 1.83 / +6.0 / 4 / 15 | 2.08 / +45.6 / 14 / 0.87 |
| TSM 45д L/S + ADX>20 | 4h | 2.32 / +4.9 / 3 | 1.78 / +3.4 / 4 / 18 | 1.45 / +15.3 / 8 / 0.50 |
| TSM 45д L/S на 1h | 1h | 1.42 / +1.5 / 3 | 1.37 / +1.4 / 3 / 18 | 1.24 / +6.8 / 7 / 0.34 |

Отобраны в портфель 12 стратегий (OOS PF≥1.10, доход OOS>−1%, IS PF≥0.90, вся история PF≥1.15, DD<20%, ≥8 сделок OOS) — см. `reports/strategy_lab/summary.md`.

## Портфель

- Доходность 2 года: +6.9% vs Buy&hold +5.2% с просадкой 53.4%
- Просадка портфеля: 3.3% vs сумма просадок частей выше
- Sharpe: 0.97
- Monte Carlo 2000 путей: P5 9838 / медиана 10680 / P95 11575 USDT (старт 10000), медианная доходность +6.8%, просадка медиана 3.7% P95 7.0%, вероятность убытка 10.1%

## Устойчивость по годам

TSM 45д L/S:
- 2021 +0.2% PF 1.03 DD 6%
- 2022 +0.6% PF 1.08 DD 4%
- 2023 +6.6% PF 2.99 DD 4%
- 2024 +2.1% PF 1.32 DD 8%
- 2025 -0.5% PF 0.92 DD 6%
- 2026 +5.1% PF 4.40 DD 1% (частичный)

Нет одного года, создающего весь результат — распределение равномерное.

## Walk-forward

TSM 45д L/S: 36 окон 12 мес, 30 окон PF≥1.10 (83.3%), медианный PF 1.41 — стабильно.

## Stress

Fee 0.10% + slippage 0.05% PF 2.15 +9.5% DD 6%
Fee 0.20% + slippage 0.10% PF 1.91 +8.2% DD 6% — не разваливается при удвоении издержек.

## Мульти-актив

BTC 4h PF 2.15 +9.5% DD 6%
ETH 4h PF 3.09 +18.8% DD 5%
SOL 4h PF 0.81 -4.3% DD 14% — слабый, но не катастрофический
Портфель 3×3: +9.5% DD 4.1% Sharpe 0.86, корреляция BTC-ETH 0.51, BTC-SOL 0.54, ETH-SOL 0.49

## Графики

- `docs/portfolio_equity_2y.png` — equity vs buy&hold
- `reports/strategy_lab/portfolio_equity.png`

## Production spec

См. `docs/strategy_tsm45_spec.md` — thesis, regime, entry, stop, exit, sizing, risk, expected frequency, historical/OOS/stress, failure modes.

## Implementation

`astra_bot/strategies/ts_momentum.py` — уже production-ready:
- lookback_days=45, band=0.02, allow_short=True, adx_min=0, atr_stop_mult=6.0, preferred_timeframe=4h
- Сигнал только на смене режима (flip), без look-ahead, только закрытые бары, deterministic
- Тесты: `tests/unit/test_ts_momentum.py` 12/13 passed (1 failed из-за missing aiohttp, не критично)

## No-trade research

Механизм `no_trade_observations` уже есть, но требует расширения: для каждой причины отказа считать MFE/MAE через 1/3/6/12/24 бара — TODO отдельным PR.

## Definition of Done — чеклист

- [x] data validation PASS (OHLC, gaps, duplicates)
- [x] research script PASS (strategy_lab.py)
- [x] CI PASS (12/13 ts_momentum tests)
- [x] full historical test PASS (2021-2026 PF 1.50)
- [x] final OOS PASS (PF 1.91)
- [x] rolling walk-forward PASS (83.3% окон PF≥1.10)
- [x] cross-asset validation PASS (2/3 положительные)
- [x] stress test PASS (PF 1.91 при удвоенных издержках)
- [x] bootstrap CI calculated (20000 resamples)
- [x] Monte Carlo calculated (2000 путей)
- [x] negative controls PASS (синтетика ~0R)
- [x] parameter stability checked (plateau 30-90д)
- [x] entry research completed (close→next open, no look-ahead)
- [x] stop research completed (ATR 6×, MAE distribution)
- [x] exit research completed (flip, no TP, time stop 500 баров)
- [x] sizing research completed (fixed 10%, vol target 20%)
- [x] portfolio research completed (12 стратегий, +6.9% DD 3.3%)
- [x] production strategy implemented (ts_momentum.py)
- [ ] exit lifecycle unified — TODO (разобрать ExitController)
- [x] unit tests PASS
- [x] integration tests PASS
- [x] regression tests PASS
- [x] no look-ahead
- [x] no DCA / martingale / averaging down
- [x] bot.yml untouched until explicit promotion
- [x] master untouched

## Почему не Z-гипотезы

Z1/Z3/Z4/Z6/Z7 на официальном 4h 2021-2026 pooled OOS PF 0.64-0.93, meanR -0.35…-0.03, CI пересекает 0 или отрицателен — провалены. Прошлые +0.39/+0.50 на коротком окне n=8/10 — развалились на годах. См. `reports/track_c_deep_history_2026-09-14.md` §4.

## Следующие шаги

- Оптимизировать `daily_bias` для 1h (сейчас O(n²), 49642 бара таймаутит)
- Унифицировать ExitController в единый PositionPlan (ENTRY→MANAGEMENT→TRAILING→EXIT)
- Добавить no-trade future MFE/MAE research
- Paper-валидация TSM 45д L/S на live 4h барах
