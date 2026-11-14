# ASTRA BOT: intraday research on USDT perpetual futures (30m)

## Decision and scope

This is a research-only candidate comparison for USDT-margined perpetual futures. It does not change the live bot, Zeus paper runner, strategy registry, account risk, leverage, position sizing, or VST/REAL routing. No candidate is promoted or executable by this research.

The requested holding profile is built into the experiment:

- preferred holding period: about 30 minutes;
- no-progress exit: close after the first completed 30m position bar when MFE is below 0.35R;
- hard maximum holding period: 18 × 30m = 9 hours;
- no 1m/5m signal logic, tick scalping, or spot instruments;
- entries use the next 30m open after a completed signal candle;
- no new signal is entered within the final 9h of available candles, so no incomplete position is force-closed and mislabeled as a full 9h time stop;
- at most one position per symbol and candidate, with no pyramiding.

The 1h context is constructed from completed 30m candles and attached with an as-of join. A signal cannot read a still-forming 1h candle.

## Market data

The runner uses BingX's public USDT-M swap REST endpoints:

- `/openApi/swap/v2/quote/contracts` to confirm active USDT-settled contracts and exchange-reported launch time;
- `/openApi/swap/v3/quote/klines` for 30m OHLCV candles;
- `/openApi/swap/v2/quote/fundingRate` for historical funding-rate events;
- `/openApi/swap/v1/market/markPriceKlines` for 4h mark-price candle opens aligned to funding settlements.

Symbols: BTC-USDT, ETH-USDT, SOL-USDT, HYPE-USDT and INJ-USDT. The loader requests history from the later of 2021-01-01 UTC or each contract's exchange-reported `launchTime`, using bounded windows of 1,000 candles. Although reference documentation describes a 1,440-candle maximum, the live BingX endpoint returned 1,000 rows in the observed run, so the research loader pins the smaller verified page size. Because the endpoint may retain less history than the contract's full lifetime, the manifest records the actual first returned candle. Empty windows before the first retained candle are skipped; any missing interval after history begins fails closed. The run ends at the latest fully closed 30m bar. Funding is requested in bounded 30-day windows (well below the 1,000-row cap), with public requests rate-limited to approximately one per second.

The data validator rejects inactive/non-USDT contracts, pre-listing candles, duplicate/out-of-order bars, missing 30m intervals, invalid OHLC, open bars, duplicate funding timestamps, non-finite rates, missing mark prices and funding coverage gaps over 12 hours. Any older gap causes the loader to exclude all earlier discontinuous funding events and candles before the latest uninterrupted segment; the exact excluded interval is recorded in the manifest. It never silently fills missing funding as zero. Actual earliest retained history is accepted only after inspecting returned data for each symbol.

This is venue-matched BingX history, but still not an execution replay: historical BBO/L2 snapshots, maker queue priority, exchange order latency and market impact are not reconstructed. A positive candle/funding result is only a candidate for VST validation with observable bid/ask and exchange fills, not permission for any live execution.

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

The run also captures a current BingX USDT-M order-book snapshot for each symbol. It estimates the round-trip cost of crossing both sides of the visible book at 1k, 5k and 10k USDT notional, then adds the 0.05% per-side standard taker fee. This is a current liquidity diagnostic only; it does not rewrite historical slippage assumptions or claim that today's book represents past fills. Incomplete visible depth is reported as unavailable for that notional, never as zero cost.

The manifest records feature coverage (valid 1h trend/ADX rows, ATR, RSI, quote-volume and taker-buy ratio), independent regime counts, and final signals by family and side. A zero-trade replay must therefore distinguish missing source fields from an over-restrictive strategy filter.

BingX v3 Kline responses may arrive as objects containing only `time/open/high/low/close/volume`, without `quoteVolume` or taker-buy volume. For those rows, quote turnover is estimated as base volume × close and used only for a relative volume-activity ratio; the source is marked as estimated. Directional expansion confirmation uses the closed candle's close-location within its high-low range, not taker flow. This is an OHLC price-action proxy, not order-book or aggressor-flow data. Array responses preserve reported quote and taker-buy fields when present.

- Signal is known only after the 30m candle closes. Entry is the next 30m bar open.
- OHLC cannot establish the true intrabar order of stop/target touches. If both are touched in one bar, the stop wins.
- A stop gap is filled at the adverse bar open reference; otherwise a barrier touch uses the stop/target reference. Both are conservative OHLC proxies, not exchange fill records.
- Every entry and exit pays an assumed 0.05% taker fee per side.
- Base adverse slippage: 0.10% per side, 0.30% total round-trip including fees.
- Stress adverse slippage: 0.15% per side, 0.40% total round-trip including fees.
- Funding cashflows are assigned from historical funding events that occur strictly after entry and at or before exit. Positive funding is paid by longs and received by shorts; negative funding is the reverse.
- The trades CSV separates gross price return, commissions, estimated slippage, funding and net return.
- Reported trade returns are unlevered. The 0.5%-per-R drawdown is a sequential trade-series proxy, not account or portfolio drawdown. Positions across symbols can overlap; concurrent exposure is not mark-to-market aggregated, and margin, liquidation and account collateral are not simulated.

The slippage assumptions are sensitivity inputs, not measured historical BingX slippage. The 0.05% taker fee matches the default fee used in the repository's perpetual cost model, but is not a verified user-specific fee tier; the owner-specific fee rate must be checked before VST comparison.

## Evaluation protocol and promotion gate

- IS descriptive period: 2021-01-01 through 2023-12-31.
- Frozen OOS period: 2024-01-01 through the last fully closed bar in the run.
- Results include aggregate IS/OOS, base/stress costs, symbol and side breakdowns, and yearly stability for 2024, 2025 and the partial 2026 period.
- Minimum evidence before considering a BingX-specific VST validation: at least 100 closed OOS positions pooled for that family, positive net expectancy and PF >= 1.30 under both base and stress costs, positive results in at least 3 of 5 symbols where sufficient history exists, separate review of LONG and SHORT, no single symbol contributing more than 50% of positive net profit, mean holding <= 90 minutes, median holding <= 60 minutes and maximum holding <= 9 hours.
- Yearly instability, a weak side, small sample, missing data, or a result that only survives one cost assumption blocks promotion.

The gate is intentionally stricter than merely PF > 1. A positive candle/funding replay only unlocks another research step: VST validation using observable bid/ask quotes, actual paper fills, current configured fee tier and matched funding. It does not authorize changing active execution.

## Outputs

The workflow uploads three inspectable files:

- report.md: summary, coverage, IS/OOS, cost sensitivity, symbol/side and yearly tables;
- trades.csv: one row per closed position per cost scenario;
- manifest.json: contract metadata, coverage, data cut-off and frozen model assumptions.

The experiment is considered incomplete unless every symbol's candles and funding pass validation and the report is generated. A failed data request must not silently fall back to spot data or another venue.
