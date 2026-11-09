# Perpetual intraday net-edge research

Status: **pre-registered research hypothesis; not a validated profitable strategy**.
Owner constraints (2026-11-09): BingX USDT-M perpetual contracts only; no spot, no scalping, no multi-day holds. Target holding time is approximately 30 minutes; hard research time-stop is 9 hours.

## Research question

Can a directional 15m pullback-continuation entry, filtered by the last fully closed 1h trend regime, produce positive net expectancy on BTC, ETH, SOL, HYPE and INJ after realistic perpetual-futures costs?

This is a new candidate family, not a claim that EMA pullbacks are profitable. It must compete against no-trade and against existing Zeus signals on the same event timestamps.

## Frozen candidate hypothesis (v0; do not optimize on OOS)

1. Market: only the five USDT-M perpetual symbols above. LONG and SHORT evaluated separately.
2. Regime: last **fully closed** 1h candle. LONG only if EMA20 > EMA50 and EMA20 is rising over the prior 3 closed 1h bars; SHORT is symmetric. No signal when trend direction is ambiguous.
3. Setup: 15m price has pulled back toward EMA20 while remaining on the trend side of EMA50. The pullback must occur within the previous 4 closed 15m bars; no chasing an extended candle.
4. Trigger: a closed 15m candle reclaims EMA20 and closes beyond the previous candle's high (LONG) / low (SHORT), with quote-volume at least 1.2x its trailing 20-bar mean. Signal is evaluated only after candle close; entry is the next bar's executable open proxy.
5. Initial stop: beyond the pullback swing extreme with a 0.15 ATR(14) buffer. Reject if stop distance is below 0.35 ATR or above 1.8 ATR; reject if expected gross reward to the next opposing structure is less than 1.5R.
6. Exit hypothesis: initial target 1.5R; stop-first if stop and target are both touched in one OHLC bar; time-stop at 36 × 15m bars (9h). Report actual holding-time distribution; the target is a median near 30m, not a forced early exit.
7. No averaging down, grid, martingale, add-ons, re-entry while the same symbol-side trade is open, or simultaneous LONG+SHORT on one symbol.

## Required execution/cost model

- Use BingX USDT-M contract data, not spot candles. Every file must identify venue, market type, symbol, interval, timezone, first/last open time, row count, duplicate count and gaps.
- Entry and exit use adverse executable-side prices where BBO/quote tape exists. If only OHLCV exists, use an explicitly labelled conservative spread/slippage proxy and never call it exchange-fill parity.
- Taker fee default sensitivity: 5 bps per side (10 bps round trip), then stress at 6 and 8 bps per side. Actual owner fee tier must replace this assumption when verified.
- Spread and slippage are charged separately only when not already represented by executable bid/ask. Avoid double-counting spread.
- Funding: apply the signed, symbol-specific BingX funding cash flow at every actual settlement timestamp crossed by the position. Missing funding coverage makes the trade/portfolio incomplete; do not silently substitute zero.
- Include partial fills only if a quote/depth tape supports them. Otherwise mark fill realism as proxy.
- Report gross PnL, fees, spread, slippage, funding and net PnL separately, per trade and in aggregate.

## Validation protocol

1. Validate candle provenance and chronology before computing indicators. Reject duplicate timestamps, impossible OHLC, non-positive prices, unexplained gaps and mismatched symbol/date data.
2. Resample/align 1h features using only the last 1h candle whose close timestamp is <= the 15m decision timestamp. No forming candles and no look-ahead.
3. Use chronological train/validation/test splits. Freeze v0 before looking at OOS. Do not select the best symbol, side, parameter set or market regime on the final test.
4. Report each symbol and side independently, plus combined portfolio with one-position-per-symbol constraints. Include trade count, PF, net expectancy in R and USDT, win rate, average win/loss, median/mean holding time, 90th percentile hold, max drawdown, exposure, monthly results and long/short breakdown.
5. Bootstrap trade sequences and run cost stress tests. A positive PF from a tiny sample is not evidence.
6. Promotion gate (research only): OOS net PF >= 1.25; positive net expectancy; at least 100 completed OOS trades combined and at least 25 per side before making side-specific claims; positive net PnL in at least 70% of OOS calendar months; max drawdown within the pre-registered risk budget; still net-positive under 1.5x base execution costs. If the sample is too small, result is INSUFFICIENT, not PASS.
7. Then run a separate BingX VST forward test with exchange-confirmed fills, fees and funding. Historical pass does not authorize live activation.

## Explicit non-goals and safety

- This is not scalping. The strategy does not target one- or two-tick moves and must clear the full round-trip cost hurdle with margin.
- No 1m/5m signal generation. 15m is the execution decision timeframe; 1h is context only.
- No 4h/1d distant targets, no 30–60 day holding periods.
- No changes to active Zeus strategy registration, risk, leverage, stops, exit engine, VST/REAL routing, secrets, workflows or live settings.
- This document is a research contract, not proof of profitability and not permission to enable the candidate.

## First implementation milestone

Build a deterministic event-driven backtest for this frozen hypothesis, with data-quality tests, no-lookahead tests, cost/funding ledger tests, and per-symbol/per-side reports. If the data archive lacks full BingX perpetual coverage or funding records, stop and report the missing evidence rather than fabricate a result.
