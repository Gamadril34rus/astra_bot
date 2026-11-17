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

### First 100-wallet API snapshot: coverage audit

The frozen panel was built from a 12 November 2026 leaderboard snapshot containing 48,023 rows. The 100-address cohort is explicitly a current snapshot, not a point-in-time historical cohort. Its overlapping selection strata are 20 high-month-PnL, 20 high-week-PnL, 20 high-month-volume, 20 high-account-value, 20 low-month-ROI and 22 residual accounts (the strata overlap; these counts are not mutually exclusive).

The first recent-window Info API retrieval returned 96,119 fills across 13 of the 100 wallets; 8 of those 13 hit the 10,000-fill API cap. The remaining 87 wallets returned no fills in that queried API window, which is not evidence that they were historically inactive. All 13 wallets with returned fills belonged to the high-month-PnL stratum; 11 also belonged to the high-week-PnL stratum. No low-month-ROI or residual wallet had fills in this short API window. This is a strong coverage/selection warning, not evidence that low-ROI wallets never trade.

Across those API-returned fills, the parsed action labels were 42,996 open, 1,336 add, 51,785 close and 2 unknown. These counts are descriptive of the returned recent-window API sample only. Reported closed PnL and fill fees are incomplete account-level performance because funding, cash flows, missing pre-cap history and TWAP slices are not fully represented. Do not rank accounts or derive a strategy from those totals.

A separate 37-day matched-trade archive is being used to observe the same frozen wallet cohort across a longer period. Its matched prints have no position inventory fields, so its output must remain a flow-behaviour study rather than an entry/exit or holding-time reconstruction.

### Fill-to-position reconstruction

The Hyperliquid Info API `userFills` / `userFillsByTime` schema can expose `startPosition`, `sz`, `side`, `dir`, `fee`, `closedPnl` and related fields for the API-returned fills. Those fields permit position-episode reconstruction only where the returned history is complete and the initial position state is known. The latest-10,000-fill cap can left-censor active accounts; TWAP slice fills are a separate endpoint and must be included before claiming complete execution history. Funding, transfers, deposits and withdrawals are not included in a fill-only PnL calculation.

The separate Ironflow public matched-trade archive contains buyer/seller, taker side, price, size, notional, market, timestamp and liquidation marker. It does **not** contain per-wallet `startPosition`, `dir`, `fee`, `closedPnl`, TWAP identifiers or account inventory. It supports wallet-side flow, size, aggression, market and time-of-day analysis, but cannot by itself prove position entry, add, reduce, close, flip, holding time or realised PnL. Keep these two evidence sources separate.

For any API wallet, reconstruct signed position from `startPosition`, `sz`, `side` and `dir`; distinguish open, add, reduce, close and flip. Group fills into position episodes. Retain fees, funding, realised PnL, partial-fill aggregation choice and missing-history flags. Never infer a position from a single isolated fill.

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


## 2026-11-12 execution update

### Hyperliquid public-wallet API pilot

A frozen stratified cohort of 100 public leaderboard addresses was captured in workflow run `43221654045`. The snapshot included 49 positive, 37 negative and 14 flat current-month PnL labels. These are current leaderboard labels, not historical point-in-time labels; comparisons remain descriptive.

A 10-wallet, 24-hour API pilot completed in run `43237604563` (artifact `13952299450`):
- 4/10 wallets had fills; 6/10 had no fills in the 24h window.
- All 4 active wallets hit the 10,000 normal-fill endpoint cap; their windows are left-censored. No TWAP slice fills were returned for these 10 wallets.
- Two active wallets' captured native-perp fills were dominated by `Close Short` (86.1% and 79.8%); no complete flat-to-flat episode was observed in that window.
- One wallet produced 157 flat-to-flat episodes, median hold about 0.66 minutes and p90 about 4.7 minutes. This is scalping-like and outside the ASTRA BOT holding-time target.
- One wallet had 10,000 total fills but only 394 native-perp fills; the remainder were HIP-3/other market identifiers.
- No account in this small pilot produced a complete, usable 30m–9h behavioural sample. Partial `closedPnl-fee` sums were not ranked; funding was not available in this endpoint sample.

Decision: do not derive a strategy from the pilot. The endpoint cap and incomplete windows are explicit exclusions.

### Historical public trade archive

Ironflow's public Hyperliquid trade archive exposes buyer, seller, taker side, market, size, price, notional, timestamp and liquidation interaction. The available files from 2026-10-03 through 2026-11-08 are 37 daily Parquet files (about 210m matched trades / 5.3 GB). Only these pre-2026-11-10 files are used under the provider-stated CC BY 4.0 terms; later files are excluded because their license changed to CC BY-NC 4.0.

The new isolated collector downloads one day at a time and filters the frozen 100-wallet cohort to native perps only (`market_class=perp`, `dex` empty/null), excluding spot and HIP-3. It writes one wallet-side row per cohort buyer/seller, deriving maker/taker from `taker_side`. Source file SHA-256, row counts, license attribution and wallet-level summaries are retained. Workflow run `43241607162` is collecting this panel; its result is not yet available.

Important limitation: the public trade archive does **not** contain per-wallet `startPosition`, `dir`, fee, `closedPnl`, `twapId` or current position state. It can establish side, size, aggression, market selection, activity bursts and time-of-day behaviour, but cannot by itself establish exact holding times or realized wallet PnL. Current leaderboard PnL groups are not historical labels. No direct-copy signals will be derived.

### Independent OKX market-data history

The official OKX archive passed a full 365-day common-window gate for BTC, ETH, SOL, HYPE and INJ USDT perpetuals:
- 2025-11-01 00:00 through 2026-11-01 00:00 UTC+8;
- 525,600 confirmed 1m bars per symbol, zero remaining minute gaps;
- 35,040 complete 15m bars and 8,760 complete 1h bars per symbol;
- settled funding maximum interval 8h; 1,095 events for BTC/ETH/SOL/INJ and 1,590 for HYPE;
- 246 overlapping REST `realizedRate` events per symbol matched the archive exactly, with zero mismatches.

One SOL minute at 2026-09-01 10:33 UTC was absent from the monthly archive and was recovered from the official `market/history-candles` endpoint. The patched row is explicitly marked `okx_rest_history_candles_gap_patch`; no synthetic price was inserted. Raw evidence is artifact `13952069656` from run `43237764108`.

A strict builder produced portfolio-compatible 15m/1h/funding files; artifact `13955310244` from run `43240467525` contains the data and SHA-256 manifest.

### First cross-venue strategy result

The frozen `15M_PULLBACK_CONTINUATION` candidate was replayed on the full OKX year with 15m signals, closed 1h context and a 9h maximum hold. This is an OHLC proxy with assumed costs, not tick-level fills.

Base costs were 5 bps fee + 2 bps spread + 3 bps slippage per side:
- IS: 141 trades, net PF 0.5088, net expectancy -0.31322%;
- validation: 32 trades, net PF 0.5415, net expectancy -0.27946%;
- OOS: 36 trades, net PF 0.8509, net expectancy -0.08606%, 33.33% positive OOS months;
- combined: 209 trades, net PF 0.5683, net expectancy -0.26893%.

At 1.5x execution costs (7.5 + 3 + 4.5 bps per side), OOS PF fell to 0.7113 and net expectancy to -0.18606%, with zero positive OOS months. The candidate fails the gate and is rejected for promotion. Small positive symbol/side PF spikes are only 2–3 trades and are not credible.

The fixed eligibility stress cost was corrected from 12.375 to 15 bps/side, matching 1.5x the declared 10 bps/side base. The coverage report now measures through the final candle close, so the exact archive is correctly reported as 365.0 days.

### Current execution state

- BingX 15m workflow `43240975536` passed the initial historical kline/trade probe and is downloading funding; it will independently screen the four regime families and open OOS only if the 12-month and IS/validation gates pass.
- OKX workflow `43240965020` is running the four regime families independently at base and 1.5x costs on the same 365-day dataset.
- Hyperliquid wallet archive workflow `43241607162` is filtering the 37-day public trade tape for the frozen 100-wallet cohort.
- No merge, no strategy registration, no VST/REAL activation, no production runtime, risk, leverage or exit-engine changes.

## 2026-11-12 decision: close the old candidate families and pivot

The owner has directed that further work on the previously tested strategy families stop. The following are **retired as research candidates** and must not be tuned, parameter-mined, promoted or run again as the active research track:

- 15m trend/pullback continuation;
- compression breakout;
- false-break reversal;
- range reversion.

This is a research-scope decision, not permission to delete or alter production strategy/runtime code. Preserve the historical reports and code for auditability. No family passed the predeclared profitability gates.

The full-year OKX OHLC-proxy family sweep completed in run `43240965020` (artifact `13954574106`). At base costs (5 bps fee + 2 bps spread + 3 bps slippage per side), OOS results were:

| Family | OOS trades | OOS net PF | OOS net expectancy/trade | Positive OOS months |
|---|---:|---:|---:|---:|
| Compression breakout | 56 | 0.4809 | -0.40216% | 0% |
| False-break reversal | 30 | 0.5079 | -0.33341% | 0% |
| Trend pullback | 44 | 0.8423 | -0.08241% | 33.33% |
| Range reversion | 34 | 0.9475 | -0.02902% | 33.33% |

At 1.5x costs, respective OOS PFs fell to 0.4052, 0.4202, 0.6909 and 0.7901; every OOS net expectancy remained negative. These are cross-venue OHLC-proxy results, not BingX execution results, but they are sufficient to stop spending research cycles on these candidates. The pullback family separately failed its own OOS test. Retain these outcomes as negative evidence; do not delete the audit trail.

## 2026-11-12: 37-day public wallet archive successfully collected

Workflow run `43246676924` completed successfully. It downloaded all 37 daily Ironflow Parquet files for 2026-10-03 through 2026-11-08, verified the provider's expected aggregate row count exactly (`210,257,579`), and recorded a SHA-256 for every source file. It then filtered the frozen 100-wallet cohort to native Hyperliquid perpetuals only, excluding spot and HIP-3 markets.

Artifacts:
- compact summary: `13957123032` (wallet archive manifest and group summaries);
- filtered wallet-side trade evidence: `13957356889` (about 503 MB compressed).

First-stage collection counts:
- 100 frozen wallet addresses; current leaderboard snapshot has 49 positive, 37 negative and 14 flat current-month PnL labels;
- 62/100 wallets had at least one matched native-perp fill in the 37-day archive;
- 23,714,766 matched native-perp trade rows involving the cohort;
- 25,170,996 wallet-side fill rows after one side row per cohort participant was materialized;
- 1,456,230 trades had both buyer and seller in the cohort;
- 37/37 daily source row counts reconcile to the dataset-card total.

Descriptive group comparison (not a historical performance attribution):
- wallets currently labelled positive month PnL: 30/49 active; median 783 fills, 41.56% taker share among active wallets, 36 active days;
- wallets currently labelled negative month PnL: 32/37 active; median 3,412 fills, 96.62% taker share among active wallets, 8 active days;
- flat group: 0/14 active in this archive.

**Do not read this as proof that passive execution causes profitability.** The PnL labels are a current leaderboard snapshot, not point-in-time labels covering the full archive. Cohort selection is retrospective and can contain survivorship bias; the observed activity differences are leads for a second-stage study, not validated alpha. A market-maker, hedger or inventory manager can have high turnover and a current negative month without being a directional loser. Taker share alone does not identify maker intent or profitability.

The next stage must use the actual wallet-side trade-level Parquet, not just the summary:
1. reconstruct each wallet × native market's signed inventory *change* through time, while flagging unknown initial inventory and never pretending the archive start is a flat position;
2. measure 5m/15m/30m/1h signed net flow, taker aggression, burstiness, scale-in/scale-out-like sequences and synchrony across the frozen cohort;
3. join those events to contemporaneous Hyperliquid market price/volume and volatility context, then test lead/lag and price response without look-ahead;
4. separate directional, liquidity-provision, hedging and high-turnover/scalping-like behaviour; exclude sub-30m patterns from ASTRA candidate rules;
5. repeat comparisons using matched activity/market-volume strata and robustly frozen cohorts; do not rank by current leaderboard PnL as if it were historical;
6. only after a behavioural pattern survives, translate it into a separate BingX-native feature hypothesis and validate against BingX data/costs. Hyperliquid is evidence about behaviour, never a direct execution signal.

The 37-day archive cannot establish complete position episodes, holding times, realised PnL, fees or funding because its schema lacks account inventory and accounting fields. The separate recent-window Info API sample is capped at 10,000 fills per wallet and is left-censored for active accounts. Use it only to cross-check behaviour for accounts/windows where completeness can be demonstrated.

**Current scope:** public large-trader behaviour reconstruction and cross-venue validation only. No strategy activation, no production changes, no VST/REAL changes, no leverage/risk changes, no PR merge.

### Second-stage wallet-flow event study implementation

The next research-only stage is implemented in `scripts/analyze_ironflow_wallet_flow.py` and wired into the isolated archive workflow. It derives:
- per-wallet × market 15m signed notional, participant notional, aggressive signed notional, taker notional, fill counts, taker counts and liquidation interactions;
- selected-cohort aggregate 15m signed/aggressive flow, participant notional, active-wallet count, net-buy/net-sell wallet counts and wallet-side fill counts;
- same-venue native-market 15m price bars from the matched trade tape, with forward close observations at 15m, 30m, 1h, 4h and 9h;
- a descriptive decile event study of signed cohort flow ratio against subsequent directional returns, requiring at least three active cohort wallets in a market/bar.

The event study is intentionally not a backtest. Observations overlap in time, are not statistically independent, and the cohort is only a selected 100-wallet sample. The output does not establish account profitability, exact positions or a tradable edge. It is a first filter for deciding which wallet behaviours merit deeper individual-wallet analysis. Aggressive flow and wallet consensus are retained in the raw 15m cohort output for follow-on tests; no thresholds are selected from this event study.

The first clean archive+event-study run is currently being verified. The previous attempts were stopped by a Ruff import-order gate before the expensive 37-day data download; the unit tests passed, and the import grouping has now been corrected. No performance result is reported until the complete workflow and artifacts are available.

### First 15m flow event study (run 43250750091)

The full 37-day collection and event-study workflow completed successfully. It produced:
- 915,238 wallet × market × 15m rows;
- 425,656 cohort × market × 15m rows and the same number of forward-return rows;
- 626,537 native-market 15m bars across 179 markets;
- 15m, 30m, 1h, 4h and 9h forward close comparisons.

The initial pooled decile result is asymmetric:
- strongest net-sell decile: flow-direction hit rate 52.24% at 15m, 52.88% at 1h, 54.51% at 4h and 55.08% at 9h;
- strongest net-buy decile: 50.18% at 15m, 49.21% at 1h, 46.91% at 4h and 45.24% at 9h.

Here `directional_hit_rate` means the future return had the same sign as the cohort's signed flow. Mean directional return for the strongest sell decile was +0.251% at 9h; for the strongest buy decile it was -0.284% at 9h. Each decile contains roughly 9,800 market/bar observations per horizon.

This is an exploratory asymmetry, **not a trading edge**. The observations overlap, are pooled across 179 markets, have only about 3.7 active cohort wallets per extreme-decile bar, and are not adjusted for market regime, symbol, time-of-day, multiple testing or fees. The study also cannot tell whether a wallet's sell was opening a short or closing a long. No signal threshold or trade rule is approved from these numbers.

### Free position-snapshot anchor discovered

A better free source for resolving that last ambiguity is the CC BY 4.0 Hugging Face sample from Hydromancer Reservoir:
- positions snapshot at 2026-10-19 00:02 UTC: 406,682 open positions across 137,190 accounts; file `snapshots/positions_2026-10-19.parquet` (18,586,255 bytes);
- matching account-value snapshot: `snapshots/account_values_2026-10-19.parquet` (58,747,857 bytes);
- schema includes user, market, signed size, entry price, liquidation price, leverage, funding PnL and account value; account values include long/short notional and account mode.

These two dated sample files are directly downloadable from Hugging Face and do not require AWS credentials or requester-pays S3 access. The full daily Reservoir archive remains requester-pays and is not being accessed.

Planned reconstruction: use the 19 Oct position snapshot as a signed-size anchor, then roll the complete 3 Oct–8 Nov Ironflow wallet-side trade tape backward and forward. This can identify flat-to-flat position episodes, adds, reductions, flips, holding times and left/right-censored positions. A small time guard around the snapshot's minute will be excluded because the published snapshot timestamp is rounded to 00:02 UTC. Validate reconstructed position sizes and, where an episode starts flat, average entry prices against the anchor. Do not claim exact net PnL: the Ironflow tape has no per-wallet fee or closed-PnL fields, and one snapshot does not provide a complete funding history.
