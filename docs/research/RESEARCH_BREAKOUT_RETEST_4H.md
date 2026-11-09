# Research breakout + retest, 4h

## Purpose and safety

This is a **research-only entry detector** based on the fixed `break` entry conditions in `scripts/research_bt_walkforward.py`. The strategy is disabled by default (`enabled=false`), is not registered in any live or Zeus pipeline, and does not alter the production strategy, sizing, stops, leverage, or VST/REAL routing.

This PR ports the signal conditions only. It does **not** port the full historical execution harness (next-bar open, 24-bar time stop, intrabar stop/target precedence, fees/slippage, and one position per symbol). The repository's normal paper/live exit controllers would produce a different complete strategy. Do not compare their results to the reported walk-forward PF as if they were the same test.

## Research provenance

`docs/research/WALKFORWARD_4H_BREAKOUT.md` records the fixed candidate:

| Metric | IS 2021–2023 | OOS 2024–2026 | Full |
|---|---:|---:|---:|
| Trades | 57 | 81 | 145 |
| Profit factor | 1.71 | 1.238 | 1.315 |
| Mean R | +0.334 | +0.131 | +0.170 |
| Win rate | 56.1% | 49.4% | 50.3% |
| Return at 0.5% risk | +9.9% | +5.3% | +12.8% |
| Max drawdown | 2.0% | 3.2% | 3.2% |

Source data: BTC/ETH/SOL 4h Binance-style klines from branch `research/binance-data`, 2021–2026. The documented cost assumption is approximately 0.18% round-trip. Raw CSV files are not present in the current master tree, so this report cannot be independently rerun from master alone. Past results are not a promise of future results.

## Entry rules ported

For a closed 4h signal bar (i), with the preceding 20 bars defining the range:

- Long: prior close is within 0.2% below the 20-bar high; signal-bar low retests within 0.8% above that level; signal-bar close finishes above it; volume is at least 1.3× its 20-bar SMA; EMA20 is above EMA50.
- Short: symmetric conditions at the 20-bar low; EMA20 is below EMA50.
- Stop distance: 1.5× simple rolling ATR(14).
- Target geometry exposed in the signal: 1.5R.

The pipeline already strips the forming candle before calling entry strategies. This implementation expects closed candles and declares `preferred_timeframe="4h"`. It uses a 200-bar warm-up to match the historical harness start index and to reduce EMA warm-up sensitivity.

## What is deliberately not done

- No addition to the active Zeus pipeline.
- No automatic activation, live entries, or changes to risk.
- No claim that signal-level parity alone reproduces the backtest.
- No promotion decision from the historical PF. A separate isolated paper runner must match the research exit/execution model, use fresh data, and pass an out-of-sample sample-size gate before this candidate is reconsidered.
