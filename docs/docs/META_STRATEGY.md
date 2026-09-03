# Meta-Strategy, EV per regime и NO_TRADE-память

Статус: реализовано и подключено к живому paper-контuru (2026-08-28).
Реализует master prompt §8/§10 и TZ §3–§6, §12–§13, §30, §32, §34.

## Архитектура (реальный поток)

```text
BingX candles (5m/15m/4h)
    → DecisionPipeline.decide()
        → RegimeEngine.classify()            # режим + confidence
        → PANIC/HIGH_VOL/UNKNOWN → NO_TRADE  # BAD_REGIME / HIGH_VOLATILITY
        → strategies → SignalCandidate[]
        → жёсткие гейты: RR, ML-prob, correlation, generic EV, liquidity
        → SignalScorer (total_score — диагностика, НЕ выбор)
        → MetaStrategy.select()              # ← выбор по EV в режиме
            → per-candidate: prior EV (R) + shrunken EV из статистики
        → Decision (LONG/SHORT/NO_TRADE + reason_code + meta-диагностика)
    → TradingEngine:
        NO_TRADE → NoTradeObservationLog.add()   # append-only, idempotent
        NO_TRADE → obs_log.enrich() на новом баре # future outcome
        LONG/SHORT → RiskEngine.check_trade() → sizing → PaperBroker
        закрытые сделки → lessons + RiskEngine.record_trade
                        + StrategyStatsStore.record()  # статистика режимов
```

## Статистика по режимам (`models/strategy_stats.json`)

Бакеты `strategy|regime|timeframe` + агрегированный `strategy|ANY|timeframe`:
`sample_size, wins, losses, sum_r, wins_sum_r, losses_sum_r, sum_mfe_r,
sum_mae_r, sum_fees, last_updated`. R = первоначальное расстояние входа-стоп,
все значения net (fees/slippage уже вычтены брокером). Обновляется на каждую
закрытую сделку в `_record_closed`; fallback: пустой бакет режима → ANY → prior.

## EV со сжатием (anti-overfitting, TZ §6)

```text
prior_r  = P(win)×AvgWinR − P(loss)×1R − costs(2 стороны, в R)
ev       = w × sample_expectancy + (1 − w) × prior_r
w        = n / (n + k),   k = 30 (ev_shrinkage_k)
confidence = w
```

- n=0 → ev=prior, confidence=0 (cold start: поведение как прежний
  `min_expected_edge_pct`-гейт, в live `min_ev_r=0.0`);
- n=3 → w≈0.09: «3 сделки, 3 выигрыша» двигает оценку с 0.4R к 0.64R,
  а не к 3.0R — единичный успех доказательством не считается (TZ §11);
- n=30 → w=0.5; n=300 → w≈0.91.

Гейты кандидата: `ev ≥ min_ev_r` (LOW_EV, отрицательный EV всегда блокирует);
при `n ≥ min_samples` дополнительно `confidence ≥ min_ev_confidence`
(LOW_CONFIDENCE). Выбор — максимум EV (score — лишь tie-break).

## Кодированные причины NO_TRADE (TZ §12)

`LOW_EV, LOW_CONFIDENCE, BAD_REGIME, RISK_LIMIT, HIGH_VOLATILITY,
LOW_LIQUIDITY, SPREAD_TOO_HIGH, CORRELATED_EXPOSURE, HALT, NO_VALID_SETUP,
INSUFFICIENT_DATA, NEWS` — в `Decision.reason_code`, в уроках и в
наблюдениях. Legacy-строки маппятся (`REASON_MAP`).

## NO_TRADE observations (TZ §12/§13)

- `models/no_trade_observations.jsonl` — append-only:
  `id (sha1(symbol|bar_time|reason|strategy|direction) — stable), symbol,
  bar_time, market_regime, regime_confidence, reason_code, reasons,
  candidate{strategy, direction, ev_r, confidence, sample_size, score},
  features{close, return_24b_pct, atr25_pct, volume_ratio}`.
  Повторная обработка того же бара дубль не создаёт (TZ §30).
- `models/no_trade_outcomes.json` — исходы по горизонтам 1/3/6/12/24 бара:
  `future_return, max_up, max_down` от close на момент отказа.
  Обогащается живым циклом, когда будущие бары уже в данных; режется до 30 дней.
  Ответ на вопрос «правильно ли система отказалась от сделки».

## Логи (TZ §32)

```text
DECISION BTC-USDT REGIME=LOW_VOLATILITY STRATEGY=scalp EV=+0.620R CONF=0.00 RISK=APPROVED SIZE=1.484
NO_TRADE BTC-USDT REGIME=LOW_VOLATILITY BEST_STRATEGY=scalp EV=-0.249R CONF=0.57 REASON=LOW_EV
RISK: вход BTC-USDT запрещён (STOP): Trading is disabled
```

## Persistence

Все три новых файла добавлены в Save state `bot.yml` — знания переживают
5-минутные CI-сессии и перезапуски.

## Тесты

- `tests/unit/test_strategy_stats.py` — buckets, PF/MFE/MAE, shrinkage,
  fallback ANY, persistence.
- `tests/unit/test_meta_strategy.py` — выбор по EV (не по score),
  regime-зависимость, блокировка отрицательного EV, small sample,
  confidence-гейт, маппинг жёстких гейтов, prior-расчёт.
- `tests/unit/test_no_trade_observations.py` — запись, дедупликация
  (включая «перезапуск» процесса), обогащение, горизонты, pruning.
- `tests/integration/test_meta_strategy_execution.py` — реальный
  production path: TRADE (candles → regime → scalp → meta → risk → broker →
  stop → lesson + stats by regime, R=−1.0 net) и NO_TRADE (LOW_EV →
  observation → повтор без дубля → future outcome).

## Известные ограничения

- Статистика накапливается только из live paper-сделок (в одном режиме
  ~несколько сделок в неделю) — до min_samples=30 выборка по-прежнему
  опирается на prior; ускорение — Phase 2 (hypothesis engine по истории).
- `candidate.timeframe` для стратегий без `preferred_timeframe` —
  фолбэк-лейбл пайплайна («1h»), а не фактический TF свечей; для
  bucket-ключей это согласованно, но не семантически точно.
