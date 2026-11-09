# Research breakout + retest, 4h

## Purpose and safety

This is a **research-only entry detector** based on the fixed `break` entry conditions in `scripts/research_bt_walkforward.py`. The strategy is disabled by default (`enabled=false`), is not registered in any live or Zeus pipeline, and does not alter the production strategy, sizing, stops, leverage, or VST/REAL routing.

This PR ports the entry detector only. It does **not** port the historical execution harness (next-bar open, 24-bar time stop, intrabar stop/target precedence, fees/slippage, and one position per symbol). The normal paper/live exit controllers create a different strategy. No performance claim is made for this implementation.

## Validation status: NOT VALIDATED

The previously published `docs/research/WALKFORWARD_4H_BREAKOUT.md` reported OOS PF 1.238 (n=81). That figure must **not** be used as evidence of an edge.

An independent data-integrity check of branch `research/binance-data` found the following CSV blob inputs:

| File | Blob SHA | Rows | Coverage |
|---|---|---:|---|
| `BTCUSDT_4h.csv` | `bf1119238982838f2ef3fd9323bc30275ac30a38` | 12,414 | 2021-01-01 to 2026-08-31 20:00 UTC |
| `ETHUSDT_4h.csv` | `8eb0a7ee2137f9da2e787e228920447c08818587` | 12,414 | 2021-01-01 to 2026-08-31 20:00 UTC |
| `SOLUSDT_4h.csv` | `5375a7af1298cf7c6ac88fc310a14bbb20b878f9` | 12,414 | 2021-01-01 to 2026-08-31 20:00 UTC |

The files have continuous 4h timestamps and valid OHLC ordering, but the prices do not match the named markets on the stated dates. For example, the BTC file's 2025-01-01 00:00 UTC open is about 12,939.98 USDT, while the public historical BTC reference for 2025-01-01 is about 93,425 USDT ([StatMuse historical record](https://www.statmuse.com/money/ask/bitcoin-price-day-by-day-january-1-2025)). The SOL file records approximately 5.16 USDT at 2024-01-01 and 1.58 USDT at 2025-01-01. These are not credible Binance BTC/ETH/SOL futures histories.

A direct implementation of the documented backtest logic over those archived files also fails to reproduce the published aggregate: it produces OOS n=196 and PF about 0.789, rather than n=81 and PF 1.238. Because the input prices fail symbol/date validation, **neither the old report metrics nor this diagnostic reproduction is a valid strategy-performance estimate**. This discrepancy is a data/provenance failure, not an invitation to tune parameters until a positive number appears.

Until official, symbol-verified raw klines are obtained and the full execution harness is independently reproduced, the candidate has no validated IS/OOS evidence and must not be promoted.

## Entry rules implemented

For a closed 4h signal bar (i), with the preceding 20 bars defining the range:

- Long: prior close is within 0.2% below the 20-bar high; signal-bar low retests within 0.8% above that level; signal-bar close finishes above it; volume is at least 1.3× its 20-bar SMA; EMA20 is above EMA50.
- Short: symmetric conditions at the 20-bar low; EMA20 is below EMA50.
- Stop distance: 1.5× simple rolling ATR(14).
- Target geometry exposed in the signal: 1.5R.

The pipeline strips the forming candle before calling entry strategies. This implementation expects closed candles and declares `preferred_timeframe="4h"`. It uses a 200-bar warm-up. The signal rules are a hypothesis port only; they are not evidence of profitability.

## Promotion gate

Do not connect this candidate to live or Zeus paper until all conditions pass:

1. Replace the unverified CSVs with source-verified official exchange klines. Record exchange, contract type, symbol, interval, download time, source endpoint, and file checksum.
2. Validate timestamp spacing, OHLC constraints, symbol-specific price scale, duplicates, missing bars, and source continuity before indicators are calculated.
3. Reproduce the frozen backtest, including next-bar execution, stop/target precedence, max-hold exit, fees, slippage, and one-position-per-symbol constraints. Store the full trade ledger and per-symbol/per-side metrics.
4. Evaluate on a frozen out-of-sample interval not used for parameter selection. Require a meaningful trade count, positive net expectancy after costs, PF above 1.2, and no single-symbol concentration.
5. Run the same full execution semantics in an isolated shadow/paper runner and compare its trades to the independent backtest before any owner-reviewed promotion.

## Explicit exclusions

- No addition to the active Zeus pipeline.
- No automatic activation, live entries, or changes to risk.
- No settings, stops, leverage, or VST/REAL routing changes.
- No performance claim from the historical PF until the data-integrity and reproducibility gates above pass.
