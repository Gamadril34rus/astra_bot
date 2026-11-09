# Research breakout + retest, 4h

## Purpose and safety

This is a **research-only entry detector** based on the fixed `break` entry conditions in `scripts/research_bt_walkforward.py`. The strategy is disabled by default (`enabled=false`), is not registered in any live or Zeus pipeline, and does not alter the production strategy, sizing, stops, leverage, or VST/REAL routing.

This PR ports the entry detector only. It does **not** port the historical execution harness (next-bar open, 24-bar time stop, intrabar stop/target precedence, fees/slippage, and one position per symbol). The normal paper/live exit controllers create a different strategy. No performance claim is made for this implementation.

## Validation status: INVALIDATED

The previous research report claimed OOS PF 1.238 (n=81). This positive result is not reproduced by the original Python backtest on the archived source files.

A pinned-data GitHub Actions workflow fetched data revision `307d684174bddc0c32c22929ab5d7411fa576d6d`, verified SHA-256 checksums, and executed the original `scripts/research_bt_walkforward.py` without modifying its trading logic:

- [Original Python reproduction, run 42593168499](https://github.com/Gamadril34rus/astra_bot/actions/runs/42593168499)
- Script commit: `8f3c2121c85f2ca112cd9ac9cf7620c69402a08d`
- Exact archived data revision: `307d684174bddc0c32c22929ab5d7411fa576d6d`

The original Python implementation returned:

| Period | Trades | PF | Mean R | Win rate | Net at 0.5% risk | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|
| IS 2021–2023 | 208 | 0.784 | -0.145 | 38.5% | -14.3% | 17.2% |
| OOS 2024–2026 | 196 | 0.789 | -0.140 | 38.8% | -13.2% | 13.7% |
| Full 2021–2026 | 413 | 0.779 | -0.148 | 38.5% | -26.9% | 29.9% |

No tested configuration in the original script passed its positive OOS PF and positive mean-R selection gate. However, these output figures are **not valid real-market performance estimates**, because the archived CSVs fail symbol/date price-scale checks.

The archived BTC series records about 12,940 USDT on 2025-01-01, while the named BTC market was around 93,425 USDT. The archived SOL series records about 5.16 USDT on 2024-01-01 and about 1.58 USDT on 2025-01-01. The CSV files are therefore not credible histories for the symbols they claim to represent. The positive report metrics are withdrawn, and the negative reproduction is only evidence that the archived report is not reproducible, not proof that this strategy loses on correctly sourced BTC/ETH/SOL data.

## Entry rules implemented

For a closed 4h signal bar, with the preceding 20 bars defining the range:

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
