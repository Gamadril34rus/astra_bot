# ASTRA BOT: intraday research on USDT perpetual futures (30m)

## Decision and scope

This is a research-only candidate comparison for USDT-margined perpetual futures. It does not change the live bot, Zeus paper runner, strategy registry, account risk, leverage, position sizing, or VST/REAL routing. No candidate is promoted or executable by this research.

The requested holding profile is built into the experiment:

- preferred holding period: about 30 minutes;
- no-progress exit: close after the first completed 30m position bar when MFE is below 0.35R;
- hard maximum holding period: 18 × 30m = 9 hours;
- no 1m/5m signal logic, tick scalping, or spot instruments;
- entries use the next 30m open after a completed signal candle;
- at most one position per symbol and candidate, with no pyramiding.

The 1h context is constructed from completed 30m candles and attached with an as-of join. A signal cannot read a still-forming 1h candle.

## Market data

The runner uses the official Binance USDⓈ-M Futures public REST endpoints:

- exchangeInfo to confirm contractType=PERPETUAL, quoteAsset=USDT, active status and contract onboarding time;
- klines at 30m resolution;
- historical funding-rate events with the recorded mark price.

Symbols: BTCUSDT, ETHUSDT, SOLUSDT, HYPEUSDT and INJUSDT. History starts at the later of 2021-01-01 UTC or the contract's exchange-reported onboard date. The run ends at the latest fully closed 30m bar.

The data validator rejects non-perpetual contracts, non-USDT quotes, pre-listing candles, duplicate/out-of-order bars, missing 30m intervals, invalid OHLC, open bars, duplicate funding timestamps, non-finite funding or missing funding mark prices. Funding coverage gaps over 24 hours are a hard failure, not a zero-funding assumption.

Binance is a reference-market proxy. It is not BingX data and cannot establish BingX execution profitability. A positive result must be reproduced with BingX VST candles/quotes/fills and BingX funding before any runtime consideration.

## Frozen candidate families

No parameter optimizer or post-result tuning is used. The three candidates represent different market conditions and are reported separately, not blended after seeing the result.

### Trend pullback

1h EMA20/EMA50 and close define directional bias. A 30m pullback approaches EMA20, then the next completed 30m candle reclaims the prior candle's high/low and closes back on the trend side of EMA20. RSI and relative quote volume are confirmation filters. Stop = 1.35 × 30m ATR(14); target = 1.50R.

### Trend expansion

The 1h trend must agree. A 30m close breaks the previous 12-bar (six-hour) high/low with relative quote volume at least 1.35× and taker-buy quote-volume imbalance on the corresponding side. Stop = 1.60 × ATR(14); target = 1.60R.

### Range reversion

The 1h context must be neutral: narrow EMA20/EMA50 spread and ADX below 22. A 30m close re-enters the Bollinger(20, 2σ) envelope after an outside close, with an RSI filter. Stop = 1.25 × ATR(14); target = the signal bar's Bollinger midline. The candidate is skipped if the target distance is below 0.85R.

All LONG and SHORT sides are evaluated separately. A per-side candidate is skipped when its stop is invalid or when the stressed round-trip cost would exceed 0.40R. This eligibility threshold is applied identically in the base and stress simulations so the two cost reports compare the same entries.

## Execution and net PnL model

- Signal is known only after the 30m candle closes. Entry is the next 30m bar open.
- OHLC cannot establish the true intrabar order of stop/target touches. If both are touched in one bar, the stop wins.
- A stop gap is filled at the adverse bar open reference; otherwise a barrier touch uses the stop/target reference. Both are conservative OHLC proxies, not exchange fill records.
- Every entry and exit pays an assumed 0.05% taker fee per side.
- Base adverse slippage: 0.10% per side, 0.30% total round-trip including fees.
- Stress adverse slippage: 0.15% per side, 0.40% total round-trip including fees.
- Funding cashflows are assigned from historical funding events that occur strictly after entry and at or before exit. Positive funding is paid by longs and received by shorts; negative funding is the reverse.
- The trades CSV separates gross price return, commissions, estimated slippage, funding and net return.
- Reported trade returns are unlevered. Margin, liquidation, portfolio-level overlapping exposure and account collateral are not simulated.

The slippage assumptions are sensitivity inputs, not measured BingX slippage. The 0.05% taker fee is a research assumption, not a verified user-specific fee tier.

## Evaluation protocol and promotion gate

- IS descriptive period: 2021-01-01 through 2023-12-31.
- Frozen OOS period: 2024-01-01 through the last fully closed bar in the run.
- Results include aggregate IS/OOS, base/stress costs, symbol and side breakdowns, and yearly stability for 2024, 2025 and the partial 2026 period.
- Minimum evidence before considering a BingX-specific VST validation: at least 100 closed OOS positions pooled for that family, positive net expectancy and PF >= 1.30 under both base and stress costs, positive results in at least 3 of 5 symbols where sufficient history exists, separate review of LONG and SHORT, no single symbol contributing more than 50% of positive net profit, mean holding <= 90 minutes, median holding <= 60 minutes and maximum holding <= 9 hours.
- Yearly instability, a weak side, small sample, missing data, or a result that only survives one cost assumption blocks promotion.

The gate is intentionally stricter than merely PF > 1. A positive Binance proxy result only unlocks another research step: BingX-specific VST validation. It does not authorize changing active execution.

## Outputs

The workflow uploads three inspectable files:

- report.md: summary, coverage, IS/OOS, cost sensitivity, symbol/side and yearly tables;
- trades.csv: one row per closed position per cost scenario;
- manifest.json: contract metadata, coverage, data cut-off and frozen model assumptions.

The experiment is considered incomplete unless every symbol's candles and funding pass validation and the report is generated. A failed data request must not silently fall back to spot data or another venue.
