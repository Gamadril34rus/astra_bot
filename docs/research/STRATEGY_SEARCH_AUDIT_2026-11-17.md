# ASTRA short-horizon strategy research audit — 2026-11-17

**Verdict: no candidate passed. Research only. No production, risk, leverage, stops, exit engine, VST or REAL changes. No PR merged.**

## 1. Frozen research contract

- Venue: BingX USDT-M perpetual swaps only. Binance/OKX/Hyperliquid observations are not treated as BingX fills.
- Signal decisions: closed 15m bars; context: last fully closed 1h bar; entry: next 15m open.
- Typical holding objective: about 30 minutes; hard maximum: 9 hours.
- No spot, no 1m/5m signal trading, no forced entry when net edge is absent.
- Base costs: taker fee 5 bps/side, spread 2 bps/side, slippage 3 bps/side. Stress: 1.5x each execution-cost component. Funding is charged at actual BingX settlement timestamps and is not multiplied in the stress case.
- Historical split is frozen: IS 2025-11-13–2026-06-20; validation 2026-06-20–2026-09-01. The 2026-09-01–2026-11-13 holdout is retired and was not used in the new screen. Fresh OOS begins 2026-11-13.

## 2. PR #343 provenance correction and canonical result

PR #343 remains closed/draft and unmerged. Its HEAD is `377c83af7851e681a2cd1ab8a9f36e33b1fa62e8` on `research/adaptive-perp-regimes`.

The canonical PR-HEAD replay is workflow run **#95 / 42771034448**, whose workflow artifact records the same branch and exact HEAD SHA. Artifact: `adaptive-bingx-perpetual-research`, ID `13743629630`, SHA-256 `babef7ce51397c679210f9f0461d5947060b799d4881827b2b5c03af7d01492f`. Replay job and all steps succeeded.

The PR description's cited run **#92 / 42692378936** is not the PR-343 HEAD run: its artifact metadata identifies branch `research/confirmed-breakout-retest` and HEAD `eb1864bb6020f32c2ad31cb7788622cd8b2694f0`. Use run #95 for PR #343's canonical result.

Run #95 covered the common 30m signal window 2025-11-06 00:00 UTC to 2026-11-10 06:00 UTC (369.25 days). It is a failed historical candidate, not a promotion:

| Split | Trades | Net PF | Net expectancy/trade | Win rate | Positive months | Gross sum | Taker fees | Spread | Slippage | Funding cost | Net sum |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| IS | 522 | 0.7009 | -0.16153% | 31.99% | 0% | +1.4309% | 52.2000% | 13.0500% | 20.8800% | -0.3793% | -84.32% |
| Validation | 146 | 0.4284 | -0.29362% | 22.60% | 0% | -18.7301% | 14.6000% | 3.6500% | 5.8400% | +0.0477% | -42.87% |
| OOS (retired for new research) | 127 | 0.5533 | -0.22370% | 29.92% | 0% | -7.5815% | 12.7000% | 3.1750% | 5.0800% | -0.1271% | -28.41% |

Funding is signed as a cost: a negative value is a funding credit. At 1.5x execution costs, OOS PF fell to **0.4543** and expectancy to **-0.30620%/trade**.

OOS family diagnostics:
- Compression breakout: 36 trades, PF 0.4904, expectancy -0.31661%; stress PF 0.4158.
- Trend pullback: 49 trades, PF 0.5644, expectancy -0.19369%; stress PF 0.4530.
- Range reversion: 27 trades, PF 0.4411, expectancy -0.31081%; stress PF 0.3632.
- False-break reversal: 15 trades, PF 1.1967, expectancy +0.05806%, but stress PF 0.9301 and expectancy -0.02444%. Fifteen trades cannot support a module claim.

The combined router's median hold was 30 minutes, mean 75.8 minutes, p90 150 minutes, maximum 9 hours, and maximum concurrency 3 in OOS. The result fails on net PF, expectancy, positive months and stress. Do not promote or re-tune against this already inspected OOS.

## 3. BingX data integrity and limitations

The 365-day archive artifact came from workflow run **43589812031**, artifact ID **14114080300**, ZIP SHA-256 `f08b16334c7dd23dde3adce7c7655c3a5bb6793afe70c00d4a85177685f569df`. Funding came from run **43595094141**, artifact ID **14113769474**, ZIP SHA-256 `052daf7e448d730f3543874f8cf771b618d81582a8e167f937414330a3d2bee6`.

- Candle manifest SHA-256: `6977cff8d2e4c834a77c9a1d7182f2684100b9542d8f00b87aa04687d0a26bde`.
- Funding manifest SHA-256: `9f8b1752b8763483d888d959924934a7cafeb85dec95d3bdd849c2ed087e175e`.
- BTC/ETH/SOL/HYPE/INJ each have 525,600 one-minute source rows, 35,040 15m rows and 8,760 1h rows for 2025-11-13 through 2026-11-13 exclusive. Minute timestamps are unique and gap-free; the 15m/1h merged files' hashes match the manifest. Native-vs-aggregated OHLC overlap differences are recorded in the manifest (15m: BTC 0, ETH 0, SOL 3, HYPE 1, INJ 0; 1h: BTC 1, ETH 1, SOL 0, HYPE 1, INJ 1).
- Funding covers the full requested interval with no imputation: 1,095 events BTC, 1,476 ETH, 1,310 SOL, 2,232 HYPE, 1,476 INJ. Maximum observed funding gap is 8h, except HYPE at 4h.
- Historical quote turnover, trade count, taker-buy volume and taker-buy quote volume are unavailable/blank in the archived K-lines. Historical OI is not available; the separate BingX OI archive is prospective only and its raw unit still needs reconciliation. No wallet identity or historical order-book depth is available. Missing flow fields are unknown, never zero.
- No Binance or OKX prices were used as BingX execution/fill data.

## 4. Additional 15m candidate screen: residual reversion vs BTC

A frozen port of the PR-352 near-miss was tested on BingX 15m bars, with closed 1h context. It used one open position per symbol, no overlapping trades, next-15m-open entry, 6-bar alt-minus-BTC residual, ±3% trigger, CLV reclaim, ATR-ratio cap 2.0, 2 ATR stop, 2.5R target, 30m/90m no-progress exits and a 9h hard stop. Funding was charged only at actual BingX settlement timestamps crossed by the position. Split-boundary entries were purged by the full 9h maximum horizon.

Two pre-declared 1h regimes were screened; neither is a candidate:

| 1h regime | Split | Trades | Net PF | Net expectancy | Win rate | Gross sum | Fees | Spread | Slippage | Funding cost | Net sum | 1.5x PF / expectancy |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Weak trend, ADX ≤22 | IS | 47 | 0.5551 | -0.45507% | 34.04% | -11.9121% | 4.70% | 1.88% | 2.82% | +0.0764% | -21.3885% | 0.4902 / -0.55507% |
| Weak trend, ADX ≤22 | Validation | 1 | 0.0000 | -0.84617% | 0% | -0.6462% | 0.10% | 0.04% | 0.06% | 0% | -0.8462% | 0.0000 / -0.94617% |
| 1h trend-aligned EMA20/50 + slope, ADX ≥20 | IS | 30 | 0.0554 | -0.84040% | 16.67% | -19.2204% | 3.00% | 1.20% | 1.80% | -0.0083% | -25.2121% | 0.0350 / -0.94040% |
| 1h trend-aligned EMA20/50 + slope, ADX ≥20 | Validation | 4 | 4.6436 | +0.97704% | 75% | +4.7001% | 0.40% | 0.16% | 0.24% | -0.0081% | +3.9082% | 3.9918 / +0.87704% |

The four positive validation trades in the trend-aligned variant are only 3 longs and 1 short, concentrated in HYPE/INJ; ETH and SOL produced no trades. The IS result is deeply negative. This is a tiny-sample false positive, not a near-pass. The weak-trend variant has only one validation trade. Both variants fail the minimum research sample and IS/validation stability requirements.

## 5. Existing 15m screens and current PR state

The later 15m screens in `BINGX_15M_EXPLORATORY_SCREENS_2026-11-16.md` and `BINGX_15M_RESEARCH_STATUS_2026-11-15.md` remain negative: impulse-rejection exhaustion, relative-strength breakout, UTC opening-range breakout, alt/BTC pair reversion, compression breakout, range reversion, prior-day sweep/reclaim and funding-extreme range sweep all fail either IS, validation, sample-size or 1.5x stress. Session-VWAP reclaim produced no eligible trades. The PR-352 30m residual candidate was a near-miss only: its 62 OOS trades were INJ-dominated (56/62), with inadequate independent-symbol coverage.

PR #347 remains open/draft on branch `research/4h-pullback-continuation`, HEAD `0fb57d8983632822a9befd5d59fda77b0900965b`. Its latest archive run **44005820963** and quality-gates run **44005821014** are successful on that HEAD. This audit branch is based on that research HEAD; no PR was merged.

## 6. OOS gate and next action

The archived 365-day data ends **2026-11-13 00:00 UTC exclusive**, exactly when the fresh OOS window starts. Therefore there are **zero fresh OOS bars** in this artifact. The 2026-09-01–2026-11-13 interval is quarantined and must not be reused for tuning or promotion. Earliest possible 90-day fresh OOS checkpoint is **2027-02-11**, and the candidate must also accumulate at least 100 OOS trades, including at least 25 per side before any side-specific claim.

Current router decision: **NO TRADE / NO PROMOTION**. Do not combine negative modules into a forced router. Next research should introduce a genuinely distinct, falsifiable hypothesis on BingX 15m/closed-1h data, screen it on IS and validation only, preserve the fresh OOS untouched, and keep the router flat whenever net edge is absent.
