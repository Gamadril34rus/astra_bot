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

## Historical gate

A candidate can only be recommended for a separate VST forward test if OOS net PF >=1.25, net expectancy >0, at least 100 combined OOS trades, at least 25 trades per side before side-specific claims, >=70% positive OOS calendar months, acceptable drawdown and positive net expectancy under 1.5x execution costs. Insufficient history or sample size is a failure to demonstrate an edge, not a pass.

OHLC fills, spread and slippage are research proxies, not exchange-confirmed fills. Historical success would not guarantee future profits. Any pass only authorizes owner review and VST planning.

## Safety

No production strategy registration, live Zeus execution, risk, leverage, sizing, production stops/exits, VST routing or REAL routing is changed. No PR merge or activation is authorized.
