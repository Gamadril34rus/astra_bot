# Crowd Liquidity Trap (CLT): research specification

**Status:** hypothesis and event-label contract only. No strategy edge is established.  
**Owner scope:** independent research; separate from Zeus DEMO/VST and all production execution.

## Research question

Does a repeatable sequence predict a 30-minute to 9-hour move on BingX USDT-M perpetuals?

1. A broad cohort of smaller participants materially increases LONG or SHORT inventory.
2. After that build begins, a separately classified large-participant cohort materially increases the opposite inventory. Closing an existing position is not an opening/addition.
3. In a later 15m bucket, at least three small-cohort wallets continue adding after the large opposite addition; same-bucket ordering is not assumed.
4. Before the break candle, price makes weak progress in the crowd's direction relative to prior-only volatility (absorption proxy).
5. A subsequent completed 15m candle breaks the relevant prior swing structure, with closed 1h context available only as a filter/regime descriptor.
6. Entry is at the next 15m bar open. No prediction of a whale entry and no intrabar look-ahead.

The SHORT setup is: crowd net LONG inventory rises, large cohort net SHORT inventory rises, upward price progress stalls, then a closed 15m candle breaks below prior structure. LONG is the exact mirror.

## Inventory accounting contract

For each wallet, market and 15m bucket, reconstruct inventory changes from the fill's position immediately before execution:

- LONG inventory delta = Open/Add Long notional minus Close Long notional.
- SHORT inventory delta = Open/Add Short notional minus Close Short notional.
- A fill that crosses through zero must be split into the closing portion and the newly opened opposite portion.

A buy can open/add LONG or close SHORT. A sell can open/add SHORT or close LONG. The Hyperliquid user-fill schema exposes `startPosition`, `side`, `sz`, `px`, and a `dir` label; use startPosition + side + size to split the fill, then cross-check the result against the compatible `dir` labels. Because API examples can use `Open Long/Short` for an add to an existing position, accept both Open and Add labels for same-side increases; never infer add-vs-open from `dir` alone. Seed the first observed fill from its own startPosition; do not assume the account was flat at the beginning of the archive. Verify that each next fill's startPosition matches the previous reconstructed position. Any mismatch, missing field, unknown direction label, API history cap, TWAP cap, or unresolved page-boundary collision invalidates that wallet-market series rather than imputing inventory.

The public Info API is bounded: userFillsByTime returns at most 2,000 fills per response and exposes only the 10,000 most recent fills per address; TWAP slice fills are a separate endpoint with its own 2,000-row ceiling. These limits mean API history coverage must be proven per wallet before using its position series. Paired public trade prints alone remain insufficient for exact inventory reconstruction.

## Participant cohorts

Do not hard-code wallet addresses or classify a wallet as a whale from one large fill. Build rolling, market-specific participant size distributions using prior-only data. Candidate classifications are small cohort and large cohort based on rolling account/position exposure quantiles, with minimum active-wallet and concentration requirements. Thresholds are selected on IS, frozen for Validation and untouched OOS. Report cohort churn and sensitivity to percentile cutoffs.

A public archive that contains paired trade prints but lacks order ID and explicit Open/Close direction cannot support this inventory reconstruction. It can support aggressor-flow controls only. Do not relabel those controls as CLT.

## Event features

All values are causal and available at the close of the event bar:

- crowd_long_inventory_delta_usd, crowd_short_inventory_delta_usd
- large_long_inventory_delta_usd, large_short_inventory_delta_usd
- crowd directional additions and opposing large-cohort additions over a bounded sequence window
- pre-break price progress in the crowd's direction, normalized by prior-only 15m volatility over elapsed bars; retain crowd notional to test progress-per-dollar as a separate feature
- 15m swing high/low calculated from bars strictly before the event bar
- closed 1h context; no unfinished 1h candle
- funding, spread and slippage inputs for execution replay

The time between crowd build and opposing large additions is measured from the training sample. Do not assume a fixed 15/30-minute delay.

## Execution and validation

- Venue for the eventual execution replay: BingX USDT-M perpetuals.
- Signal bars: 15m; context: closed 1h. 1m may only aggregate execution/cost data.
- Entry: next 15m open after the structure-break close.
- Holding window: evaluate 30m through 9h; no scalping.
- Include actual funding where available and conservative fees, spread and slippage. Stress at 1.5x costs.
- Use non-overlapping events and day/market-clustered uncertainty estimates.
- Split chronologically into IS, Validation and untouched OOS/walk-forward. Tune only on IS; freeze before Validation/OOS.
- Controls/ablations: price-only structure break; crowd-only; large-opposite-flow only; aggressor-side-only proxy; simple large-wallet-follow baseline; remove absorption; remove structure confirmation; shuffled event times.
- Report both directions, each market, regimes, trades, expectancy, PF, drawdown, cost sensitivity, sample count and confidence intervals.
- Minimum promotion screen: at least 100 OOS trades, PF >= 1.25, positive expectancy at 1.5x costs, no single market/regime carrying the entire result. This is a research gate, not a promise of profitability.

## Current data limitation (do not bypass)

The frozen 37-day Ironflow paired-trade archive has wallet addresses and aggressor side, but no order ID, explicit position direction, fees or realized PnL. It cannot establish whether a wallet opened/added or closed/reduced a position. The recent 24h public-API audit covers only a selected subset and is an interpretation check, not historical reconstruction. The earlier 32-event BingX flow replay is not a CLT backtest and its candidate-minus-control interval crossed zero.

Therefore the first gate is **historical inventory-data feasibility**. Until a complete, auditable historical position-direction series is available, the model may be tested on synthetic fixtures and aggressor-flow controls only. No CLT performance claim, parameter tuning, or strategy promotion from those proxies.


## Historical inventory feasibility pilot

A research-only pilot is being added for the frozen 2026-10-03 through 2026-11-08 cohort. It selects only addresses whose manifest fill count is at or below the 10,000-fill API ceiling and whose top markets include one of BTC/ETH/SOL/HYPE/INJ. It retrieves direction-aware fills and TWAP slices, validates startPosition continuity, and builds 15m inventory deltas. The first 7 days are reserved for a prior-only position-size calibration; later bars are used for event discovery.

The pilot's small and large groups are relative to the selected leaderboard sample (lower half vs upper quartile of median position notional), not a market-wide retail/whale census. It is a feasibility diagnostic only. If too few continuous wallet-market histories remain, the correct result is insufficient data, not a weakened completeness gate.


## 2026-11-20 inventory feasibility update

The first 100-wallet archive-manifest pilot completed but produced no calibrated cohort: 19 continuous wallet-market pairs were spread across markets, with fewer than four eligible calibration candidates in each market. Its 15m aggregate CSV contained only a header. This is a data-coverage failure, not evidence against or for CLT.

The follow-up workflow samples 120 addresses deterministically across monthly-volume deciles from the public Hyperliquid leaderboard JSON, then fetches the 2026-10-03 to 2026-11-09 individual-fill history. The public leaderboard is an undocumented snapshot and is not a representative census. All fills remain read-only research data; any address with API/TWAP caps, page-boundary ambiguity, ambiguous same-timestamp ordering, direction-label mismatch or position discontinuity is excluded. The run must report calibrated wallet counts and non-empty market buckets before any event study can proceed.

## 2026-11-20 BingX analytics endpoint discovery (not yet admitted to dataset)

A read-only source review found a potentially better venue-native data path for the next CLT feasibility gate. A BingX change feed reports these newly added USDT-M perpetual endpoints on 2026-11-18:

- `GET /openApi/swap/v1/openInterest/history`
- `GET /openApi/swap/v1/longShortRatio/history`
- `GET /openApi/swap/v1/liquidation/statistics`
- `GET /openApi/swap/v1/liquidation/heatmap`
- `GET /openApi/swap/v1/liquidation/levels`

The first two are the highest-priority candidates: historical OI and long/short-ratio series could provide market-wide positioning context directly from the execution venue. They do not reveal individual participant identities or prove that a specific cohort opened versus closed inventory. They can validate/condition the CLT hypothesis, not replace the event's explicit inventory accounting.

Important distinction: BingX's separate Trading Data announcement lists `/openApi/swap/v2/quote/marketOpenInterest`, `/openApi/swap/v2/quote/topTraderLongShortRatio`, and liquidation snapshot endpoints, and explicitly says those v2 quote analytics routes return only the latest snapshot and require signature verification. Do not assume those snapshot semantics apply to the separately announced v1 `/history` routes.

A preliminary unauthenticated smoke request to the three v1 routes returned BingX error `100413`. The earlier note attributed this to a missing `X-BX-APIKEY`, but that conclusion was too strong: the request did not include BingX's documented `X-SOURCE-KEY: BX-AI-SKILL` source header, which the official API skills guide says is required on all requests, including public market-data calls. The official skills guide also says ordinary swap market-data routes are public and do not require an API key/HMAC, but it does not currently list these new v1 analytics routes. Therefore the v1 routes' access class is **unverified**; do not request or configure private API credentials on the basis of this smoke test. No credentials were used, and no private/trading endpoint was called.

Admission rule: first verify the exact v1 route, method, parameters, schema and authentication class against BingX's current official API docs or an official API response. Repeat a read-only probe with the documented `X-SOURCE-KEY` header. Only if BingX explicitly documents these routes as signed/private should an owner-configured read-only API key be used. Then verify response code, units, timestamps, interval, pagination/history depth, symbol coverage and repeatability. Preserve raw response metadata and aggregate-only research outputs. Until those checks succeed, treat these endpoints as an unverified lead, not available historical data.

Source notes: BingX's 2026-11-20 Trading Data announcement documents the separate v2 quote analytics routes and says they require signature verification and return only the latest snapshot: https://bingxservice.zendesk.com/hc/en-001/articles/18417651640079-BingX-Launches-Liquidation-Map-and-Trading-Data-with-Full-Support-on-App-Web-and-API. BingX's official AI API skills guide says the `X-SOURCE-KEY: BX-AI-SKILL` header is required on all requests, including public endpoints, and distinguishes public market data from authenticated private routes: https://github.com/BingX-API/api-ai-skills/blob/main/skills/references/authentication.md. The five v1 analytics route names are currently found in a third-party BingX change feed, not yet in the official skills endpoint table: https://www.microversesystems.com/feeds/bingx.html.

## 2026-11-20 prospective BingX snapshot-capture design

BingX's official 2026-11-20 announcement confirms three potentially useful network-wide snapshot feeds for perpetual futures: market-wide open interest, top-trader long/short ratio, and liquidation statistics. The snapshots refresh approximately once per minute. Their v2 quote endpoints require signature verification, are rate-limited to one request per second, and do not support historical queries.

This changes the data plan:

1. Do not expect backfill from these snapshot routes. They can only build a prospective dataset from the moment a recorder starts.
2. For the five CLT markets (BTC, ETH, SOL, HYPE, INJ), a complete round across three feeds is 15 requests. At the published one-request-per-second limit, reserve at least 15 seconds plus jitter for each round; schedule no faster than one round per minute and honor response headers/errors.
3. Persist each response as an immutable observation with local receive time, exchange timestamp if present, endpoint, symbol, schema version, and raw response hash. Store raw snapshots separately from normalized aggregates. Never fill missing observations by interpolation.
4. Track coverage per symbol/feed, duplicate timestamps, stale snapshots, schema changes, and missing-symbol responses. A gap is missing data, not a flat value.
5. Treat open interest as market-wide leverage/participation context, not direction by itself. Treat top-trader ratios as a large-participant proxy, not a direct measure of retail cohort inventory. Compare account/trader/position dimensions where returned. Liquidation statistics are rolling-window amounts; do not sum repeated overlapping snapshots as if they were independent liquidation events.
6. Treat liquidation maps/heatmaps as estimated potential liquidation concentrations, not realized liquidation prints or guaranteed support/resistance.

The prospective CLT validation should compare: (a) price up + OI up/down, (b) top-trader accounts vs positions ratio divergence, (c) crowd/large directional positioning proxy, and (d) subsequent BingX structural break and net outcome. The new feeds may enrich the context layer, but they do not replace the explicit cohort-inventory requirement in the original event definition.

Collector implementation is gated on the exact official endpoint parameter/response schemas and a safe read-only key. Any key must have read-only permission, IP allowlisting where practical, and no trade/withdraw permissions. Credentials must be stored only as repository/environment secrets, never committed or pasted into chat. Until configured, the collector remains a design only and the existing research stays offline.

## Hyperliquid pagination audit: boundary recovery correction

The first 120-wallet feasibility artifact completed successfully (2026-10-03 through 2026-11-09): 208,365 API fill rows, 6,552 TWAP slice rows, 74 wallets with core-market fills, 81 continuous wallet-market pairs before considering cohort sufficiency, and 54 position-continuity failures. Only BTC produced a minimally calibrated cohort (17 wallets: 11 small, 6 large); HYPE had 5 calibrated wallets, below the six-wallet minimum; ETH, SOL and INJ had no calibrated cohort. This was a feasibility pilot, not an event study. In the pre-correction artifact's 15-minute test phase, BTC had 10 large-short-add buckets and 18 large-long-add buckets. In the preceding 12 bars, the maximum number of distinct small-cohort wallets adding LONG before a large SHORT add was 1; the corresponding maximum for small SHORT adds before a large LONG add was 2. No large-add bucket met the minimum three-distinct-small-wallet crowd condition. HYPE's five calibrated wallets were below the six-wallet cohort minimum; its small cohort showed no short-add activity in the test phase. These observations mean the full hypothesized sequence was not observed in this sample, not that the strategy has been disproven.

That artifact also flagged 27 wallet histories at a page-boundary timestamp. Review of the collector found a conservative but incomplete boundary check: it only flagged a collision when multiple fills at the final timestamp were already visible on the page. A single visible fill could still have additional same-millisecond fills omitted by advancing the next request to `last_time + 1`.

The collector now probes each full page's final timestamp using the API's inclusive `startTime=endTime=last_time` query, merges the complete exact-time response, and records recovered markets. If that exact-time response itself reaches the 2,000-row response cap, is inconsistent, or appears shorter than the boundary rows already seen, the wallet history remains fail-closed and is excluded. This is an additional request per full page and must be accounted for in rate-limit pacing. Regression tests cover both recovery when only one boundary fill appeared on the full page and unresolved exact-timestamp truncation.

The published first artifact predates this correction. Its 27 collision flags, cohort counts and event-window observations must not be treated as final after the fix. Re-run the same date window and sampling protocol with the corrected collector, then compare recovered/unresolved boundaries, continuity failures, per-market eligible cohorts and rolling opposing-add counts. Important: the workflow currently downloads a fresh public leaderboard snapshot on every run, so the selected 120-wallet cohort is not frozen and the before/after comparison is not paired. Do not attribute differences solely to the pagination fix; first consider a privacy-safe reproducibility mechanism for the sampled cohort, or interpret the rerun as a new feasibility sample. Hyperliquid documents a 2,000-fill response limit and a 10,000 most-recent-fill history ceiling for `userFillsByTime`; these remain hard coverage constraints: https://www.hyperliquiddocs.io/for-developers/api/info-endpoint.html.

## 2026-11-20 follow-up collector audit

The official Hyperliquid `userTwapSliceFills` response wraps each slice as `{"fill": {...}, "twapId": ...}`. The collector previously unwrapped `fill` but dropped the sibling `twapId`, even though its TWAP de-duplication key uses that identifier. The collector now preserves the outer `twapId` on the normalized fill, with a regression fixture containing otherwise-identical slices from two TWAP orders. Official response example: https://hyperliquid.gitbook.io/Hyperliquid-docs/for-developers/api/info-endpoint.

The previous continuity counter could also include artificial mismatches: when a same-millisecond group had no unique position-transition ordering, the series was correctly invalidated but its fills were still traversed in arbitrary API order. Continuity diagnostics now count only transitions inside uniquely ordered groups, reset the chain anchor across ambiguous groups or invalid position fields, and report the number of checked transitions, mismatch rate, and distinct wallet-market pairs with a mismatch. Ambiguous same-time groups remain excluded; they are not silently repaired.

The first feasibility artifact and the pagination-corrected run started before this follow-up code change. Their continuity-failure counts and TWAP cohort aggregates are not final. A fresh run on the corrected collector is required.

## 2026-11-20 price-only swing-break control

Using the public BingX 15-minute OHLC archive for 2026-10-03 through 2026-11-09, a price-only control was evaluated on BTC, ETH, SOL, HYPE and INJ. A signal is a close outside the prior eight completed candles' swing high/low; signals are separated by a 36-bar cooldown; entry is the next 15-minute open. The same 415 events were retained at every horizon so each trade had a complete nine-hour follow-up. Mean gross directional returns were +3.59 bps at 30m, +5.98 bps at 1h, +2.72 bps at 2h, +3.23 bps at 4h, and +0.95 bps at 9h. A three-day moving-block bootstrap over UTC dates (20,000 resamples, preserving cross-market clustering within sampled date blocks) produced 95% intervals that crossed zero at every horizon: [-3.21, 11.86], [-1.26, 14.88], [-6.43, 13.09], [-9.28, 16.54], and [-20.13, 22.31] bps, respectively.

This is a baseline control only, not a CLT result. It is gross of fees, spread, slippage and funding. Excluding HYPE reduced mean returns to +2.75, +1.23, -5.78, -5.18 and -1.26 bps across the same horizons. The current control therefore has no statistically robust edge in this short sample and is not a substitute for the cohort-inventory event test.


## 2026-11-20 cross-venue alignment and execution-cost baseline

The archived signal/source venue is Hyperliquid; the target execution venue is BingX USDT-M perpetuals. The 2026-10-03 to 2026-11-09 archive contains 3,552 exactly timestamp-matched 15m candles per market and 888 1h candles per market. On 15m bars, candle direction agreed between venues in 94.3%–97.2% of observations. For an eight-bar swing-break rule, same-direction break overlap over the union of break events was:

| Market | 15m candle direction agreement | 15m break overlap | 1h break overlap | Median signed close basis (bps) | 95th percentile absolute basis (bps) |
|---|---:|---:|---:|---:|---:|
| BTC | 96.57% | 90.58% | 96.03% | -1.15 | 3.81 |
| ETH | 96.23% | 90.22% | 93.02% | -0.82 | 3.67 |
| SOL | 95.92% | 90.42% | 92.86% | -0.83 | 4.09 |
| HYPE | 97.16% | 87.81% | 92.07% | +6.01 | 12.86 |
| INJ | 94.26% | 89.35% | 92.76% | -1.17 | 13.71 |

No opposite-direction swing-break observations occurred in this archive, but venue-only break events did occur. HYPE has a persistent positive close basis; INJ has a materially wider tail basis. Keep per-market results separate and report venue-only breaks explicitly. The alignment is sufficient to justify a cross-venue research experiment, not to treat Hyperliquid and BingX prices as interchangeable.

BingX's published base perpetual fees are 0.02% maker and 0.05% taker per execution. A two-sided round trip is therefore approximately 4 bps maker/maker or 10 bps taker/taker, before spread, slippage and funding. On the price-only control above, gross pooled mean returns of +3.59, +5.98, +2.72, +3.23 and +0.95 bps at 30m, 1h, 2h, 4h and 9h become approximately -0.41, +1.98, -1.28, -0.77 and -3.05 bps under maker/maker fees, or -6.41, -4.02, -7.28, -6.77 and -9.05 bps under taker/taker fees. These are illustrations using published base rates, not a claim about the owner's actual account tier or fee discount. Funding, spread and slippage remain excluded, and the original block-bootstrap confidence intervals still cross zero before any costs.

Fee source: BingX Perpetual Futures fee schedule, https://bingxservice.zendesk.com/hc/en-001/articles/11263240298255-Perpetual-Futures-Fee-Schedule. The actual account commission rate must replace these base assumptions before a final strategy decision.
