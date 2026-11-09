# Confirmed 30m breakout/retest hypothesis

Status: independent exploratory hypothesis; not validated, not selected, and not enabled.

## Objective

Test a different entry mechanism from the existing compression-breakout and trend-pullback families: wait for a confirmed range escape, then require price to retest and hold the broken level before entering. The purpose is to avoid buying the initial impulse candle and reduce failed-break exposure.

This hypothesis is for BingX USDT-M perpetual futures only. It is not a scalping strategy. Entries are evaluated on closed 30m candles with the latest closed 1h context.

## Frozen signal rules

1. Define the breakout level from the highest high / lowest low of the preceding 20 completed 30m candles, excluding the breakout candle.
2. The breakout candle must close beyond that level. Its quote volume must be at least 1.3 times the average quote volume of the preceding 20 completed candles. The breakout candle is not included in its own volume baseline.
3. Within the next 1-3 completed 30m candles, price must retest the level:
   - LONG: the candle low reaches the level or up to 0.15 ATR above it; the candle closes at or above the level, no more than 0.50 ATR above it, and closes green.
   - SHORT: the candle high reaches the level or up to 0.15 ATR below it; the candle closes at or below the level, no more than 0.50 ATR below it, and closes red.
4. The hourly context must agree: ADX14 >=18, EMA20 above EMA50 and rising over three closed 1h bars for LONG; the inverse for SHORT.
5. Any intervening close back through the broken level invalidates the setup. Only the most recent qualifying breakout in the three-bar retest window is considered.
6. Entry proxy is the next 30m open after the retest candle closes.
7. Stop is beyond the lowest low / highest high from the breakout aftermath through the retest candle, with a 0.15 ATR buffer. The shared simulator rejects risk below 0.35 ATR or above 1.8 ATR.
8. Fixed target is 2R. The shared no-progress invalidations remain 30m at MFE <0.35R, 90m at MFE <0.75R, and a hard 9h maximum hold.

## Validation contract

- Reuse the same BingX candle and funding archive, position overlap rules, execution cost ledger, funding settlement logic, OHLC stop-first fill convention, and 60/20/20 chronological split as the baseline runner.
- Run both the base cost case and 1.5x execution-cost stress. Eligibility must not change between the two runs.
- Inspect trade count, PF, net expectancy, win rate, drawdown, positive months, side and symbol distribution, exit reasons, and holding-time distribution.
- Treat fewer than 100 OOS trades as insufficient evidence. A result shorter than 12 months of common five-symbol history remains exploratory and cannot pass the existing promotion gate.
- No parameter search or rule changes are allowed after examining OOS results. Any new iteration requires a new versioned hypothesis and a fresh chronological holdout.
- A historical pass can only justify a separate BingX VST forward test. It does not authorize production activation or REAL trading.

## Known limitations

- The entry, stops and exits are OHLC proxies, not exchange-confirmed fills.
- Quote-volume ratios can vary by contract and market regime.
- A three-bar retest window and 2R target are hypotheses, not empirically proven optimum values.
- The current implementation reuses the common portfolio simulator; only the signal selector differs.
