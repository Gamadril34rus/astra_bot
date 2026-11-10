# 4h pullback continuation hypothesis

Status: frozen exploratory candidate, not validated and not selected.

## Hypothesis

A 30m pullback into the EMA20/EMA50 trend zone followed by a closed-bar reclaim may offer a better entry than buying a 30m impulse. The direction is permitted only when the last fully completed 4h regime agrees.

## Frozen rules

- Signal timeframe: 30m. Regime timeframe: 4h, aggregated from eight contiguous completed 30m candles.
- A 4h candle is available only at its close. Incomplete 4h groups are discarded. The 4h context is merged backward on decision_time; no partial 4h candle may influence a signal.
- LONG regime: 4h EMA20 > EMA50, EMA20 rising over three completed 4h candles, ADX14 >=18.
- SHORT regime: inverse EMA direction and slope, ADX14 >=18.
- Pullback window: preceding six closed 30m bars must touch the 30m EMA20 zone while holding the EMA50 structure (within 0.25 ATR).
- Trigger: current closed 30m bar reclaims EMA20 and closes beyond the preceding bar's high/low in the regime direction. Current candle must be directional.
- Relative volume >=1.2x the preceding 20 completed 30m bars; the signal candle is excluded from the baseline. Use quote volume when positive, otherwise base volume within the same symbol.
- Entry proxy: next 30m open.
- Stop: beyond the lowest/highest of the six pullback bars by 0.15 ATR; shared risk filter 0.35-1.8 ATR.
- Target: fixed 2R.
- Shared funding ledger, base costs, 1.5x stress eligibility, stop-first OHLC convention, 30m/90m no-progress exits, 9h hard cap and chronological 60/20/20 split.

## Validation

- No parameter tuning on OOS. The rules are frozen before replay.
- Use all five BingX USDT-M perpetual symbols, latest uninterrupted funding segment only, no funding imputation.
- At least 12 months of common five-symbol coverage and >=100 OOS trades are required before considering promotion; PF >=1.25, positive net expectancy, >=70% positive months, per-side coverage, acceptable drawdown, and positive net under 1.5x costs are also required.
- The currently available common five-symbol history is only 218.81 days, so a replay on the present archive is exploratory and cannot pass the 12-month gate.
- OHLC fills are proxies, not exchange-confirmed fills. Any historical pass would only justify a separate VST forward test.

## Safety

Research-only. No production config, active Zeus strategy, risk, leverage, sizing, stops, exit engine, VST/REAL routing or secrets are changed.
