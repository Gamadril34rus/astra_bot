# 15m pullback continuation with closed 1h context

Status: frozen research hypothesis; exploratory until the historical gate is met. Research-only, not selected or enabled.

## Market and timing

- BingX USDT-M perpetual swaps only: BTC-USDT, ETH-USDT, SOL-USDT, INJ-USDT and HYPE-USDT.
- Signals are calculated only on closed 15m candles. Entry proxy is the next 15m open.
- Context is the latest fully closed 1h candle, merged backward on the 15m decision timestamp. No forming 1h candle is visible.
- No 1m/5m signals, spot, scalping, pyramiding, grid or forced trades.

## Frozen hypothesis

- 1h regime: ADX14 >=18; LONG requires EMA20 > EMA50 and EMA20 rising over three closed 1h bars. SHORT is symmetric.
- Pullback: one of the preceding four closed 15m bars touches the 15m EMA20 zone while closing structure remains beyond EMA50 within a 0.25 ATR allowance.
- Trigger: current closed 15m bar reclaims EMA20, closes beyond the previous bar high/low, and is directional.
- Relative volume >=1.2x the mean of the preceding 20 closed 15m bars. The signal candle is excluded. Use positive quote volume; otherwise use base volume within that symbol only.
- Stop: four-bar pullback swing plus/minus 0.15 ATR; reject risk outside 0.35-1.8 ATR.
- Target: fixed 1.5R.
- Shared stop-first OHLC handling, 30m and 90m no-progress invalidation, and a 9h hard maximum. No signal means NO_TRADE.

## Cohorts and evidence

- Primary cohort: BTC, ETH, SOL and INJ. Their latest uninterrupted BingX funding segments overlap from 2025-09-02, giving more than 12 months of common history through 2026-11-09.
- HYPE is evaluated separately because its latest uninterrupted funding segment begins 2026-04-05. It cannot be silently mixed into a 12-month five-symbol claim.
- Candle and funding archives must be from BingX swap endpoints, with recomputed SHA-256, zero candle gaps, and no funding imputation. The exact first/last timestamps and hashes are written to the run artifact.
- Chronological 60/20/20 IS/validation/OOS. Any trade crossing a split boundary is excluded, not assigned by entry time alone. Rules are frozen before OOS is read.
- Report gross return, taker fees, spread, slippage, funding and net return separately; report PF, expectancy, win rate, hold distribution, positive-month share, drawdown and concurrent exposure by cohort, family, symbol and side.

## Prior research and provenance corrections

- The four-family 30m router in the adaptive-perpetual artifact was negative: OOS PF 0.5406, net expectancy -0.24751% per trade, 74 OOS trades, and no positive OOS months. It is rejected, not a tuning target.
- The earlier 30m lab artifact from PR #344 run #102 reported OOS PF 0.563 (trend pullback), 0.692 (trend expansion) and 0.526 (range reversion); the corresponding stress PFs were 0.477, 0.590 and 0.438. No family passed.
- Important provenance correction: adaptive artifact run #92 (ID 42692378936) was executed on SHA `eb1864bb6020f32c2ad31cb7788622cd8b2694f0` on branch `research/confirmed-breakout-retest`, not on PR #343 HEAD `377c83af7851e681a2cd1ab8a9f36e33b1fa62e8`. PR #343 HEAD has no associated Actions run. The raw 30m/1h candle hashes in that artifact were independently recomputed and matched its manifest, and all listed candle gap counts were zero; however, that artifact contains no 15m candles. The new candidate workflow therefore downloads fresh 15m/1h swap candles and funding from BingX rather than treating run #92 as PR #343 CI evidence.
- The latest uninterrupted funding starts in the verified archive were BTC 2025-07-09, ETH 2025-08-29, SOL 2025-04-11, INJ 2025-09-02 and HYPE 2026-04-05. The four-symbol core has more than 12 months of common coverage; HYPE does not and is evaluated separately.

## Holdout protection

The workflow first publishes IS and validation only. The OOS summary is not generated unless both IS and validation have PF >=1.0 and positive net expectancy in both base and 1.5x cost cases. If this screen fails, the workflow stops with OOS unopened; the next hypothesis must be developed using IS/validation only. HYPE is screened separately and does not enter the four-symbol OOS promotion cohort because its uninterrupted funding history is shorter than 12 months.

## Historical gate

A candidate can only be recommended for a separate VST forward test if OOS net PF >=1.25, net expectancy >0, at least 100 combined OOS trades, at least 25 trades per side before side-specific claims, >=70% positive OOS calendar months, acceptable drawdown and positive net expectancy under 1.5x execution costs. Insufficient history or sample size is a failure to demonstrate an edge, not a pass.

OHLC fills, spread and slippage are research proxies, not exchange-confirmed fills. Historical success would not guarantee future profits. Any pass only authorizes owner review and VST planning.

## Safety

No production strategy registration, live Zeus execution, risk, leverage, sizing, production stops/exits, VST routing or REAL routing is changed. No PR merge or activation is authorized.
