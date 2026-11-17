# BingX 15m exploratory screens — 2026-11-16

**Status: research-only; no candidate passed.** No strategy registration, production routing, VST/REAL, risk, leverage, stop or exit-engine changes were made. Nothing in this report authorizes a merge.

## Scope and split discipline

- Venue: BingX USDT-M perpetual swaps only. Symbols: BTC, ETH, SOL, HYPE, INJ.
- Decisions: closed 15m bars; context: last fully closed 1h bar; entry: next 15m open; maximum holding: 540 minutes. No 1m/5m signal logic.
- IS: 2025-11-13 00:00 UTC to 2026-06-20 00:00 UTC.
- Validation: 2026-06-20 00:00 UTC to 2026-09-01 00:00 UTC.
- 2026-09-01 to 2026-11-13 is a retired historical holdout and is not used for promotion. Fresh OOS begins 2026-11-13 and requires at least 90 complete days. No OOS returns were calculated in these screens.
- The local exploratory scripts clipped 15m, 1h and funding inputs at the exclusive validation end **before** indicator, rolling-statistic or signal computation. The fixed split was not moved.

## Data provenance and quality

Source artifacts:
- BingX 1m OHLCV archive: workflow run **43589812031**, artifact **14114080300** (bingx-usdm-1m-12m-ef6627ec256ba9be32ae8caaf16a8213618af4b5).
- BingX funding archive/merge: workflow run **43595094141**, merged artifact **14113769474** (bingx-usdm-funding-merged-63fcfec57e4104bcb1449a75bb22debc81c28819).
- Candle manifest SHA-256: 6977cff8d2e4c834a77c9a1d7182f2684100b9542d8f00b87aa04687d0a26bde.
- Funding manifest SHA-256: 9f8b1752b8763483d888d959924934a7cafeb85dec95d3bdd849c2ed087e175e.

Each symbol has 525,600 1m bars, 35,040 15m bars and 8,760 1h bars over 365 UTC days; the 1m archive reports zero missing and zero duplicate minute timestamps. The merged 15m/1h series uses native BingX bars where timestamps overlap and complete 1m-derived aggregates elsewhere. Small OHLC close mismatches remain recorded in the manifest (15m: SOL 3, HYPE 1; 1h: BTC 1, ETH 1, HYPE 1, INJ 1); native bars override the corresponding aggregates. These are price-rounding differences, not missing-bar repairs.

The latest uninterrupted funding segments cover the full requested 365-day window with no imputation: BTC 1,095 events (8h cadence), ETH 1,476 (8h), SOL 1,310 (8h), HYPE 2,232 (4h), INJ 1,476 (8h). Older segments outside the latest contiguous window are excluded.

**Important data limitation:** BingX's archived kline payload contains only OHLCV. The verified manifest marks quote volume, trade count, taker-buy base/quote volume, historical order book, historical open interest and wallet identity as unavailable. A public-history probe found historicalTrades fromId requests returning recent prints rather than the requested historical interval. Thus the current dataset cannot support a valid historical BingX taker-delta, large-print, order-book, OI or liquidation-footprint test.

## Cost convention for these exploratory screens

Per side, base costs are 5 bps taker fee + 2 bps spread + 3 bps slippage (10 bps/side; 0.20% round trip). Stress is 1.5x: 7.5 + 3 + 4.5 bps/side (15 bps/side; 0.30% round trip). Funding is applied from actual BingX settlement timestamps, without forward-fill or imputation. Costs are explicit proxies, not observed fills.

Drawdown below is the equal-notional trade-sequence drawdown proxy in percentage points, using $1,000 total notional per trade (pair trades split $500/$500 across legs). It is not an account-level or compounded portfolio drawdown. Concurrent exposure is the maximum number of simultaneously open test positions/pairs.

## New IS/validation hypotheses screened

All screens below used the stricter cost convention above and were evaluated separately by symbol and side. PF is net PF. Stress PF is shown as IS / validation.

| Hypothesis | IS trades | IS PF / expectancy | Validation trades | Validation PF / expectancy | 1.5x PF IS / val | Median hold IS / val | Positive months IS / val |
|---|---:|---:|---:|---:|---:|---:|---:|
| 15m impulse-rejection exhaustion | 87 | 0.804 / -0.0785% | 18 | 0.095 / -0.4227% | 0.613 / 0.059 | 45 / 30 min | 25% / 0% |
| Cross-sectional relative-strength breakout | 830 | 0.634 / -0.1950% | 229 | 0.589 / -0.1918% | 0.507 / 0.450 | 30 / 30 min | 0% / 0% |
| UTC opening-range breakout | 755 | 0.500 / -0.2034% | 258 | 0.487 / -0.1738% | 0.360 / 0.328 | 30 / 30 min | 0% / 0% |
| Alt/BTC relative-value pair reversion | 509 | 0.343 / -0.1905% | 165 | 0.236 / -0.1791% | 0.219 / 0.124 | 30 / 30 min | 0% / 0% |

### PnL component audit, base costs

Summed percentage points across the equal-notional trades; each cost column is a deduction.

| Hypothesis / period | Gross | Taker fee | Spread | Slippage | Funding contribution | Net |
|---|---:|---:|---:|---:|---:|---:|
| Impulse rejection / IS | +10.613 | -8.700 | -3.480 | -5.220 | -0.046 | **-6.833** |
| Impulse rejection / validation | -3.999 | -1.800 | -0.720 | -1.080 | -0.010 | **-7.609** |
| Relative-strength breakout / IS | +3.918 | -83.000 | -33.200 | -49.800 | +0.260 | **-161.823** |
| Relative-strength breakout / validation | +1.901 | -22.900 | -9.160 | -13.740 | -0.016 | **-43.915** |
| Opening-range breakout / IS | -2.541 | -75.500 | -30.200 | -45.300 | -0.009 | **-153.550** |
| Opening-range breakout / validation | +6.818 | -25.800 | -10.320 | -15.480 | -0.051 | **-44.833** |
| Relative-value pair / IS | +4.889 | -50.900 | -20.360 | -30.540 | -0.048 | **-96.960** |
| Relative-value pair / validation | +3.442 | -16.500 | -6.600 | -9.900 | -0.001 | **-29.559** |

### Failure diagnosis

- **Impulse rejection:** IS long expectancy was -0.1242%; short was only +0.0251% before stress. In validation both sides were negative (7 long, 11 short). HYPE's small IS gain (36 trades) reversed sharply in validation (8 trades); INJ's apparent IS gain was only 6 trades and had no validation trade. IS/validation trade-sequence DD was -10.184/-6.932 percentage points at base; max concurrent positions 3/2. This is both weak and under-sampled.
- **Relative-strength breakout:** both sides and all four alt symbols were negative in IS and validation; HYPE was the worst contributor. Base trade-sequence DD was -165.685/-46.413 points; max concurrent 4/3. Gross gains were negligible relative to fees, spread and slippage.
- **Opening-range breakout:** both directions and all five symbols were negative in both periods; HYPE was worst, while INJ was merely less negative. Base DD was -156.215/-46.374 points; max concurrent 5/4. The signal's gross edge was already negative in IS and did not cover execution costs in validation.
- **Relative-value pair:** all four alt/BTC pairs and both pair directions were negative in both periods. Base DD was -97.589/-28.979 points; max concurrent 4/4. Most exits were no-progress or adverse z-score stops, not successful convergence.
- None of these screens meets even the IS/validation gate, let alone the untouched-OOS promotion gate. No symbol/side subset is promoted from small isolated samples.

## Funding-price divergence event study

A separate event study selected extreme BingX funding observations using a trailing 90-day percentile computed strictly from prior settlements, then required a contrary/non-confirming closed 1h four-hour return. Entry was the first 15m open at/after settlement. It is an overlapping fixed-horizon event study, **not** a position-managed strategy backtest.

At the 0.20% base round-trip cost, IS / validation PF by horizon was:
- 30m: 0.426 / 0.389
- 1h: 0.576 / 0.507
- 4h: 0.817 / 0.570
- 9h: 0.766 / 0.558

Expectancy was negative at every horizon in both periods; 1.5x costs worsened all results. Sample: 1,093 IS and 260 validation events per horizon. Because observations overlap, portfolio drawdown and concurrent exposure are not meaningful for this event study. This hypothesis is rejected.

## Existing session-VWAP research run

Workflow run **43715029908**, head **5684a1e52a4734af18e5c0efe3587d56b32c30c2**, completed successfully. It found 352 IS and 108 validation raw signals, but **zero completed trades**: every signal was rejected by risk, cost or target-geometry checks. The IS/validation gate failed and OOS remained UNOPENED. Its declared base cost (5 + 1.25 + 2 bps/side) is less conservative than the 5 + 2 + 3 bps/side convention used in the new screens; standardize this before any future candidate comparison. The run's no-trade result does not depend on opening OOS.

## Cross-venue wallet evidence: hypothesis source only

The 37-day Ironflow/Hyperliquid archive (2026-10-03 through 2026-11-08) contains 210,257,579 source rows and 425,656 cohort 15m flow observations for a current-snapshot 100-wallet cohort. The descriptive flow study shows little useful separation at 15m/30m; a stronger contrarian pattern appears at 1h–9h for extreme cohort flow. It is not a validated strategy: observations overlap, the cohort is not point-in-time selected, matched prints do not establish complete wallet positions, and Hyperliquid fills are not BingX fills.

A separate snapshot-anchored position reconstruction produced 18,337 episodes (15,968 complete), median complete-episode hold 18.12 minutes and p90 1,516 minutes, but **0 of 1,013** anchor-entry price checks matched and 171 wallet-market groups were boundary-ambiguous. These figures are not a reliable institutional holding-time or entry/exit model. Do not copy trades or infer that these wallets are institutions.

## Decision and next research action

No candidate passes. The most interesting behavioural clue is contrarian response to extreme aggressive flow over 1h–9h, but the required BingX historical taker-flow fields are absent from the verified archive. The next useful step is to obtain an owner-approved, licensed BingX USDT-M historical trade/aggregate-flow archive (and, if available, timestamped OI/liquidation history), verify its coverage and schema, then test a 15m auction-transition hypothesis with closed 1h context and price-only placebo ablations. Do not substitute Binance/OKX prints or treat Hyperliquid wallet activity as BingX execution data.

Until that data is available, avoid another broad indicator-parameter search: it would increase selection bias without addressing the missing explanatory variables. Keep the router no-trade capable. Historical OOS and fresh OOS remain closed; no VST planning recommendation is justified yet.
