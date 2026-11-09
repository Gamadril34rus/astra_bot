# Previous UTC-day range breakout hypothesis

Status: pre-registered exploratory research candidate. Not validated, not selected, and not enabled.

## Research question

Does a first 30m close outside the previous completed UTC day's high/low have a sufficiently persistent directional move to overcome BingX USDT-M perpetual execution costs when filtered by the closed 1h trend?

This candidate differs from the 20-bar compression breakout and the breakout/retest candidate: its structural reference is the prior full UTC day's range, not a rolling intraday range. It trades the first confirmed close outside that daily boundary; it does not enter the impulse intrabar.

## Frozen rules

1. Calculate the high and low from the 48 completed 30m candles of the previous UTC calendar day. The current UTC day's bars are never included in those levels.
2. LONG: the current 30m candle closes above the prior-day high; the immediately preceding 30m close was at or below that level; the current candle is green.
3. SHORT: the current 30m candle closes below the prior-day low; the immediately preceding close was at or above that level; the current candle is red.
4. Only the first close outside each side of the previous-day range is eligible per UTC day. Returning inside and breaking the same level again does not create a second attempt in that direction.
5. Current 30m base/quote volume must be at least 1.5 times the average of the preceding 20 closed 30m candles. The current candle is excluded from the baseline. Use quote volume when positive; if BingX omits it and the archived value is zero, fall back to base volume. Compare relative volume within each symbol only.
6. The last completed 1h context must agree: ADX14 >=20, EMA20 above EMA50 and rising over three closed 1h candles for LONG; inverse for SHORT.
7. Entry proxy is the next 30m open after the breakout candle closes.
8. Stop is beyond the lowest low / highest high of the preceding four completed 30m bars by 0.15 ATR. Apply the shared risk filter of 0.35-1.8 ATR and the shared 1.5x stress-cost eligibility filter.
9. Fixed target is 2.5R. Reuse the common 30m no-progress invalidation at 30m if MFE <0.35R, 90m if MFE <0.75R, and hard 9h maximum hold.
10. Use the same funding settlement ledger, base execution costs, 1.5x stress case, OHLC stop-first fill convention, and chronological 60/20/20 split as the shared portfolio runner.

## Why test this candidate

A fixed prior-day range is a market structure level independent of the rolling 20-bar breakout already in the existing router. The setup is designed to participate only after a completed candle has closed through the boundary, not on an intrabar wick. The 1h trend and relative-volume filters aim to reject low-energy excursions.

This is a hypothesis, not an assertion that previous-day highs/lows predict returns. It may fail because daily boundaries can become crowded liquidity targets, and a 30m close confirmation can still enter after most of the move has occurred.

## Validation contract

- Source data must be BingX USDT-M perpetual 30m / 1h candles and actual symbol-specific funding rates.
- Validate exact UTC day aggregation, complete 48-bar prior-day range, no missing candles, no lookahead, first-break-only behavior, long/short symmetry, funding continuity, and source checksums.
- Run the frozen candidate on the same complete five-symbol sample as the parent router. Do not pool pre-listing periods or infer missing funding as zero.
- Report IS and validation first; retain OOS as untouched holdout until the candidate is frozen and the validation process is accepted.
- Minimum promotion evidence remains >=12 months of common five-symbol coverage, >=100 OOS trades, PF >=1.25 and positive net expectancy on OOS, >=70% positive months, per-side sample checks, mean hold <=90m / median <=60m / max <=9h, acceptable drawdown, and positive net performance under 1.5x costs.
- An undersized OOS or validation sample is INSUFFICIENT, not PASS. A profitable historical replay only justifies a separate BingX VST forward test. It does not authorize paper/REAL activation.

## Known limitations

- OHLC entry and exit fills are proxies, not exchange-confirmed fills.
- Base volume is a within-symbol relative-volume proxy when quote volume is absent; it is not comparable between contracts.
- UTC daily boundaries are a research convention, not a claim that crypto markets have a natural daily close.
- The fixed 2.5R target and thresholds are frozen for this hypothesis; do not tune them after examining OOS.

## Safety

Research-only. No production config, active Zeus strategy, risk, leverage, position sizing, stops, exit engine, paper/REAL routing, or secrets are changed.
