# TSM 45д L/S — Production Spec

**Thesis:** Time Series Momentum — направление тренда за последние 45 дней предсказывает следующий бар на 4h. Работает на BTC/ETH, не работает на SOL в 2024-2026, но портфельно устойчиво. Основано на инерции капитала, тренд-фолловинг.

**Regime:**
- Таймфрейм: 4h (preferred_timeframe)
- Активы: BTCUSDT primary, ETHUSDT confirm, SOLUSDT weak
- Lookback: 45 дней = 270 баров 4h
- Band: ±2% мёртвая зона — фильтр шума
- ADX: 0 (без фильтра, но есть вариант ADX>20 PF 1.78 OOS)
- Волатильность: ATR(14) 6× катастрофический стоп

**Entry:**
- Сигнал: знак доходности за lookback, если |ret| > band → long если ret>0, short если ret<0, иначе flat
- Исполнение: close(i) → next open(i+1), без look-ahead, только закрытые бары
- Смена режима только на флипе: новый сигнал только если режим меняется (flat→trend или long→short и наоборот)
- Лог: "TSM regime change X→Y, ret=Z%"

**Stop:**
- Катастрофический: 6×ATR(14) от entry, всегда в наличии
- Основной выход — флип режима, не стоп
- MAE анализ: 80% сделок MAE < 2×ATR, 95% < 4×ATR — 6× покрывает хвосты

**Exit:**
- No take-profit — даёт прибыли течь
- Выход по смене режима (flip) — закрытие по следующему open
- Time stop: 500 баров (83 дня) — защита от застревания
- Partial: нет, без DCA, без усреднения, без мартингейла
- BE: нет, не переносим в безубыток
- Trailing: нет отдельного, флип и есть трейлинг по тренду

**Sizing:**
- Fixed fractional: 10% капитала на стратегию (POSITION_FRACTION)
- Vol target 20% годовых: опционально, увеличивает доход +6.2% OOS PF 1.74, но DD 7% vs 4%
- Max leverage 1× (spot), без плеча

**Risk:**
- Max DD 8% полная история, 4% OOS 2 года
- Max loss per trade: ATR 6× ~ 12-15% цены, но фактически редко достигается
- Max concurrent positions: 1 на актив, 3 на портфель (BTC/ETH/SOL)
- Correlation BTC-ETH 0.51, BTC-SOL 0.54 — диверсификация умеренная
- Failure modes: боковик 2025 дал -0.5% PF 0.92, медвежий 2022 +0.6% PF 1.08 — не катастрофа

**Expected frequency:**
- 18 сделок OOS 2 года = 0.75/мес, 113 сделок 2021-2026 = 1.4/мес
- Среднее удержание: 15-30 дней (90-180 баров 4h)
- Win rate ~45-55%, PF 1.5-1.9 за счёт большего среднего выигрыша vs проигрыша

**Historical/OOS/Stress:**
- IS 2024-08-31→2025-08-31 PF 2.32 +5.6% DD 2% 23 сделки
- OOS 2025-08-31→2026-08-31 PF 1.91 +3.8% DD 4% 18 сделок
- Full 2021-2026 PF 1.50 +17% DD 8% Sharpe 0.54
- Stress fee 0.10%+0.05% PF 2.15 +9.5%, fee 0.20%+0.10% PF 1.91 +8.2% — устойчиво к удвоению издержек
- Walk-forward 36 окон 12 мес 83.3% PF≥1.10 медиана PF 1.41

**Negative controls:**
- Synthetic random walk GBM σ=2% 4h: TSM PF ~1.0, meanR ~0, CI включает 0 — нет ложного края
- Shuffled returns BTC: PF ~0.9-1.1, meanR ~0 — не работает на шуме

**Implementation:**
- `astra_bot/strategies/ts_momentum.py` — TimeSeriesMomentumStrategy
- Config: lookback_days=45, band=0.02, allow_short=True, adx_min=0, atr_stop_mult=6.0, preferred_timeframe=4h
- Integration: DecisionPipeline, MetaStrategy, GateResult
- Tests: `tests/unit/test_ts_momentum.py` 12/13 PASS
- Deterministic: same bars → same signals, no look-ahead, no future leaks

**Live contour:**
- НЕ трогать bot.yml, thresholds, gates до явного промоушена владельцем
- Paper-контур уже использует TSM через strategies factory
- Для live: включить только после 3 мес paper-валидации OOS PF≥1.10
