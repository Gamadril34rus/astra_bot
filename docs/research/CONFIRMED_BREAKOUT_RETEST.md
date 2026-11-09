# Confirmed 30m breakout/retest hypothesis

Status: independent exploratory hypothesis; not validated, not selected, and not enabled.

## Objective

Test a different entry mechanism from the existing compression-breakout and trend-pullback families: wait for a confirmed range escape, then require price to retest and hold the broken level before entering. The purpose is to avoid buying the initial impulse candle and reduce failed-break exposure.

This hypothesis is for BingX USDT-M perpetual futures only. It is not a scalping strategy. Entries are evaluated on closed 30m candles with the latest closed 1h context.

## Frozen signal rules

1. Define the breakout level from the highest high / lowest low of the preceding 20 completed 30m candles, excluding the breakout candle.
2. The breakout candle must close beyond that level. Its volume must be at least 1.3 times the average of the preceding 20 completed candles. Use quote volume when positive; if BingX omits quoteVolume and the archived field is zero, fall back to base volume. The breakout candle is not included in its own volume baseline.
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
- Quote-volume ratios can vary by contract and market regime. When BingX's historical endpoint omits quoteVolume, base contract volume is used only as a same-symbol relative-volume proxy; absolute volume is not compared across symbols.
- A three-bar retest window and 2R target are hypotheses, not empirically proven optimum values.
- The current implementation reuses the common portfolio simulator; only the signal selector differs.


## Preliminary partial replay: not a pass

A local diagnostic replay was run against the surviving Run #93 archive for BTC, ETH and SOL only. It uses the same closed-bar signal rules, ATR/ADX formulas, 30m entry proxy, funding segment validation, stop-first OHLC convention, no-progress exits, 9h cap, risk filters and cost formulas as the shared portfolio runner. It is not the canonical five-symbol Actions report.

The first diagnostic attempt produced zero signals because BingX's archived `quote_volume` column is zero throughout these files. That attempt was invalid and discarded. The runner now falls back to base `volume` when `quote_volume` is zero, matching the shared feature pipeline; a regression test covers this archive shape. Relative volume is calculated within each symbol only.

### Coverage and split

- Instruments: BTC-USDT, ETH-USDT and SOL-USDT. HYPE and INJ candle pairs were not present in the partial artifact and are excluded.
- Common valid candle/funding interval: 2025-12-26 00:00 UTC through 2026-11-09 19:00 UTC, 318.79 days.
- Chronological split: IS through 2026-07-05 06:36 UTC; validation from that boundary; OOS starts 2026-09-07 00:48 UTC.
- This is less than 12 months and only three instruments, so it cannot satisfy the promotion contract.

### Results

| Metric | Base costs | Costs ×1.5 |
|---|---:|---:|
| Completed trades in common interval | 29 | 29 |
| Profit factor | 0.8100 | 0.6607 |
| Net expectancy per trade | -0.07796% | -0.16046% |
| Win rate | 24.14% | 24.14% |
| Median / mean hold | 60 / 100.3 min | 60 / 100.3 min |
| P90 / max hold | 216 / 510 min | 216 / 510 min |
| Net sum of trade returns (unweighted) | -2.2610% | -4.6535% |

Gross PF was 1.2934 and gross mean return was +0.08771% per trade, but average execution costs plus funding were 0.16568% per trade at base costs. The gross movement did not pay for execution. At 1.5x costs the deficit widened, with the same 29-trade population.

Exit reasons: 7 targets, 8 stops, 11 exits after 30m without progress, and 3 exits after 90m without sufficient progress. The no-progress exits averaged -0.3051% net per trade at base costs. Mean hold also exceeded the 90-minute target at 100.3 minutes.

The OOS slice contains only 3 trades. Its apparent PF of 8.91 at base costs is statistically meaningless at this sample size; it must not be treated as evidence of an edge. Validation contains only 2 trades. The in-sample 24 trades had PF 0.692 and expectancy -0.13122% per trade.

**Decision:** do not select or tune this hypothesis from the partial replay. The candidate is economically negative across the 29-trade common sample and more negative under cost stress. Keep the rules frozen; the full five-symbol archive run is still required to decide whether this family should be retired or whether the partial result is materially distorted by the missing instruments. No production or VST activation is authorized.
