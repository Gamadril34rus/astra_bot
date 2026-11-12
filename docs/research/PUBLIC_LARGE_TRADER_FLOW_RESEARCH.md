# Public large-trader flow research — source audit and experiment design

**Status:** research-only. No strategy registration, live/VST/REAL changes, risk/leverage changes, or production exit changes.

## Executive finding

There is no public, identity-resolved trade tape for hedge funds on BingX USDT-M perpetuals. Centralized exchanges expose anonymous market prints and aggregate market state; account-specific fills are private to the account owner. Therefore the study must not claim to reconstruct named institutions' BingX entries/exits from public CEX data.

Use three separate evidence classes and never mix them as if they were equivalent:

1. **Public account-level fills on transparent perpetual venues** — useful to study how selected high-performing public wallets enter, add, reduce, close, reverse, and hold. These wallets are not proven institutions, and the venue is not BingX. Treat as a behavioural hypothesis source only.
2. **Regulatory institutional positioning** — CFTC COT/TFF aggregates positions by trader category for CME futures. This identifies categories such as Asset Manager/Institutional and Leveraged Funds, not individual traders or fills. Data is weekly, as of Tuesday, normally released Friday; backtests must timestamp availability at release, never Tuesday.
3. **BingX-native anonymous flow and derivatives state** — actual BingX market data: taker buy/sell volume, trade count, open interest, funding, mark/index premium, liquidation/force-order data where available, order-book imbalance and spread. This is the correct venue for final signal execution and cost validation, but it does not identify counterparties.

## Source audit

| Source | What is actually observable | Use | Do not claim |
|---|---|---|---|
| BingX USDT-M public trades and historicalTrades | Anonymous price, size, time, buyer-maker flag; no account identity | Venue-native signed flow / large-print clusters if history can be recovered | That a print belongs to a named fund or whale |
| BingX swap klines | OHLCV, quote volume, trade count, taker-buy volume | 15m/1h price and aggressive-flow features | Individual trader fills |
| BingX open interest, funding, premium, depth/book ticker | Aggregate positioning/carry and current liquidity state | Regime and crowding filters; live monitoring | Long/short identities or historical order-book state unless archived |
| Hyperliquid public leaderboard + Info API `userFills` / `userFillsByTime` | Public wallet addresses, individual fills, direction, size, price, time, fee, closed PnL; current open positions via account-state endpoint | Build a transparent-wallet behaviour panel; infer entry/add/reduce/close episodes and holding times | That a wallet is a regulated institution, that its fills are BingX fills, or that its execution costs transfer to BingX |
| CFTC COT / TFF futures-only (CME Bitcoin contract) | Weekly category-level long/short/spread positions, changes and concentration | Slow macro positioning context; test whether extreme positioning changes the prior probability of 15m/1h setups | Intraday entries/exits or individual fund identities |
| SEC Form 13F | Quarterly reported holdings in Section 13(f) securities, filed up to 45 days after quarter end | Not selected for this perpetual-futures strategy | BTC perpetual trade history; 13F is not an intraday derivatives tape |

Primary references:
- BingX swap market API reference: https://github.com/BingX-API/api-ai-skills/blob/main/skills/swap-market/api-reference.md
- Hyperliquid Info API: https://hyperliquid.gitbook.io/Hyperliquid-docs/for-developers/api/info-endpoint
- Hyperliquid public leaderboard: https://stats-data.hyperliquid.xyz/Mainnet/leaderboard
- CFTC COT methodology and release timing: https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm
- CFTC TFF futures-only public dataset: https://publicreporting.cftc.gov/stories/s/TFF-Futures-Only/98ig-3k9y/
- SEC Form 13F scope: https://www.sec.gov/rules-regulations/staff-guidance/frequently-asked-questions-about-form-13f

## BingX endpoint evidence and current data blocker

Research run **43213211531** on PR #347 HEAD `a5198037a544ae32d057749e195d84a2c586baf6` completed successfully on 2026-11-12. Its immutable artifact is `15m-pullback-continuation-research`.

The archive manifest requested history from 2021-01-01 but the effective common 15m signal window is only **2026-08-04 12:30 UTC through 2026-11-12 11:45 UTC (99.97 days)**. All archived 15m/1h CSV checksums and zero-gap checks passed; this proves integrity of the downloaded slice, not completeness of the requested historical period. OOS remained unopened and was correctly skipped.

The BTC public-history probe tested BingX `historicalTrades` against 2025-12-26 while varying `fromId` from 1 to near the latest ID and one million IDs earlier. All requests returned approximately the latest 1,000 prints around 2026-11-12 11:53 UTC, not the requested historical region. This observed behavior makes the endpoint unsuitable as a verified historical backfill until a reproducible pagination test proves otherwise. The probe also found 1m candles for sampled windows from 2025-12-26 onward, but not for 2025-08-08; sparse point samples do not prove continuous 1m coverage.

The next data gate is therefore:
- probe 1m history around 2025-11-12, 2025-12-01, 2025-12-26 and monthly windows through 2026-11 for all core symbols;
- if the source returns contiguous minute bars, aggregate only complete groups of 15 closed BingX 1m candles into 15m OHLCV, preserving quote volume, trade count and taker-buy volume; verify against native 15m bars wherever they overlap;
- require at least 365 continuous common days for the core cohort before OOS can be opened;
- if 1m depth is insufficient, stop pretending that 15m history is complete. Seek owner approval for a licensed BingX trade-data archive; do not substitute Binance candles or fills.

## Wallet-panel study design (research only)

### Cohort selection without survivorship leakage

- Snapshot the public Hyperliquid leaderboard at fixed historical cut times where possible; save raw response, timestamp, checksum and schema.
- Select accounts using only information available before each study window. Rank by a combination of realised PnL, ROI, traded notional and account-value floor; require repeated profitable windows and adequate fill count. Keep a separate high-volume/market-maker-like cohort rather than selecting only highest ROI.
- Freeze the address cohort before evaluating subsequent periods. Do not retroactively select today's winners and backtest their past trades as if they were known winners then.
- Record public display name only as metadata, not as proof of institutional identity. Exclude subaccounts/agent addresses when they cannot be reliably mapped to the controlling account.

### Fill-to-position reconstruction

For every address and perpetual coin, persist raw fills with source, retrieval time and hash. Reconstruct signed position from `startPosition`, `sz`, `side` and `dir`; distinguish open, add, reduce, close and flip. Group fills into position episodes. Retain fees, funding, realised PnL, partial-fill aggregation choice and missing-history flags. Never infer a position from a single isolated fill.

Measure:
- entry timing relative to 15m/1h structure, VWAP/anchored VWAP, prior session high/low, range expansion and volatility compression;
- whether large accounts add into adverse movement or only after confirmation;
- exit timing relative to liquidity sweeps, momentum decay, funding settlement and volatility contraction;
- holding-time quantiles, scale-in count, average adverse/favourable excursion and net PnL after the venue's own costs;
- lead/lag between cohort net position changes and subsequent 15m/1h returns, conditional on market regime.

A public wallet cohort is a cross-venue behavioural sample, not a direct BingX signal. Transfer only robust, repeated behavioural rules into BingX-native tests.

## BingX-native institutional-footprint feature set

For each closed 15m decision bar, with the last fully closed 1h context only:

- **Aggressive flow:** taker-buy quote volume minus inferred taker-sell quote volume; delta / total quote volume; rolling CVD slope; unusually large prints relative to symbol-specific rolling distribution.
- **Participation:** trade count, quote volume, average trade size, volume z-score and volume concentration across the bar.
- **Position build/unwind:** change in open interest aligned to price return (price up + OI up; price down + OI up; price up + OI down; price down + OI down). Do not label these quadrants as certain long/short opening because OI is aggregate.
- **Crowding/carry:** funding z-score and percentile, mark-index premium, time to next funding settlement.
- **Liquidity:** spread, top-of-book and multi-level depth imbalance, depth depletion/refill, sweep/reclaim around session extremes. Historical order-book features are allowed only if archived contemporaneously; current snapshots cannot be backfilled.
- **Liquidation pressure:** BingX-native liquidation/force-order observations when endpoint coverage is verified; treat missing liquidation history as missing, not zero.
- **Structure:** session VWAP and anchored VWAP, previous day/week high-low, value-area proxy only if the underlying trade/volume data is real and complete, 1h trend/range regime, 15m compression and false-break state.

All rolling features are computed from data available at the decision timestamp. The 1h candle must be fully closed. No Binance market data is permitted as a substitute for BingX fills or price path.

## Candidate hypothesis to test after data is fixed

Do not create a standalone “follow whales” strategy. Test a **BingX flow-confirmed auction transition** as an additional independent module:

1. Detect a 15m test of a prior session extreme, range boundary or 1h value/VWAP zone.
2. Require a measurable taker-flow imbalance and abnormal participation, but reject if OI/price behaviour indicates a crowded late chase or if spread/depth makes net reward inadequate.
3. Enter only after a 15m close confirms reclaim/rejection and the next 15m open; no 1m/5m signal decisions.
4. Use 1h context to distinguish continuation from failed auction; allow long and short as separate tests.
5. Exit on structural invalidation, momentum/flow reversal, no-progress at 30m/90m, or hard 9h maximum. No forced trade when expected net edge after fees, spread, slippage and funding is non-positive.

Compare against price-only baseline and shuffled/signed-flow placebo features. Require improvement in validation net expectancy and stress PF, not merely higher gross returns. Evaluate each feature ablation to establish whether flow adds information beyond price/volume. Preserve untouched OOS.

## Required experiment outputs

For each module, symbol, side and cohort:
- gross PnL, taker fees, spread, slippage, funding, net PnL;
- trade count, net PF, net expectancy, win rate;
- holding-time median/p90/max, positive-month share, drawdown, concurrent exposure;
- flow-feature coverage and missingness, source provenance and raw archive hashes;
- base costs and 1.5x stress costs;
- chronological IS/validation/OOS boundaries and no-lookahead assertions.

Promotion remains blocked until the existing gate is met: OOS net PF >= 1.25, positive net expectancy, >=100 combined OOS trades, >=25 per side before side-specific claims, >=70% positive OOS calendar months, acceptable drawdown, and positive net expectancy at 1.5x costs. A pass permits owner review and separate VST planning only.
