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

The collector now probes each full page's final timestamp using the API's inclusive `startTime=endTime=last_time` query, merges the complete exact-time response, and records recovered markets. If that exact-time response itself reaches the 2,000-row response cap, is inconsistent, omits any fill key already visible at the page boundary, or appears shorter than the boundary rows already seen, the wallet history remains fail-closed and is excluded. Equal row counts alone are not sufficient: the exact-time response must contain every unique boundary fill key from the full page. This is an additional request per full page and must be accounted for in rate-limit pacing. Regression tests cover recovery when only one boundary fill appeared on the full page, unresolved exact-timestamp truncation, and a same-size probe that omits one observed fill while returning a different fill at the same timestamp.

The published first artifact predates this correction. Its 27 collision flags, cohort counts and event-window observations must not be treated as final after the fix. Re-run the same date window and sampling protocol with the corrected collector, then compare recovered/unresolved boundaries, continuity failures, per-market eligible cohorts and rolling opposing-add counts. Important: the workflow currently downloads a fresh public leaderboard snapshot on every run, so the selected 120-wallet cohort is not frozen and the before/after comparison is not paired. Do not attribute differences solely to the pagination fix; first consider a privacy-safe reproducibility mechanism for the sampled cohort, or interpret the rerun as a new feasibility sample. Hyperliquid documents a 2,000-fill response limit and a 10,000 most-recent-fill history ceiling for `userFillsByTime`; these remain hard coverage constraints: https://www.hyperliquiddocs.io/for-developers/api/info-endpoint.html.

## 2026-11-20 follow-up collector audit

The official Hyperliquid `userTwapSliceFills` response wraps each slice as `{"fill": {..., "tid": ...}, "twapId": ...}`. The collector now preserves the outer `twapId` as TWAP-order lineage and uses the unique inner `tid` as the fill-level de-duplication key. A regression fixture covers two distinct fills within one TWAP order and a duplicate delivery of the same trade ID. Official response example: https://hyperliquid.gitbook.io/Hyperliquid-docs/for-developers/api/info-endpoint.

The previous continuity counter could also include artificial mismatches: when a same-millisecond group had no unique position-transition ordering, the series was correctly invalidated but its fills were still traversed in arbitrary API order. Continuity diagnostics now count only transitions inside uniquely ordered groups, reset the chain anchor across ambiguous groups or invalid position fields, and report the number of checked transitions, mismatch rate, and distinct wallet-market pairs with a mismatch. Ambiguous same-time groups remain excluded; they are not silently repaired.

The first feasibility artifact and the pagination-corrected run started before this follow-up code change. Their continuity-failure counts and TWAP cohort aggregates are not final. A fresh run on the corrected collector is required.

A further audit on 2026-11-20 found a separate aggregation defect in `audit()`: parsing and cohort-calibration exposure collection were indented outside the per-fill loop. When multiple fills shared one millisecond, continuity checked the complete ordered group, but the parsed inventory retained only its final fill. This understated activity, exposure samples and potentially changed cohort eligibility. The per-fill parsing, direction-label validation, parsed-record append and calibration exposure update now all execute inside the fill loop. Regression test `test_audit_keeps_every_fill_in_a_multi_fill_timestamp_group` constructs six continuous BTC wallets with three uniquely ordered same-millisecond fills each and verifies that all 15 fills from the five cohort-assigned wallets reach calibration totals.

Consequently, inventory artifacts from run `44808263231` (head `eb9c5e86`) and run `44810126611` (head `4619f5b8`) are superseded and must not be used for cohort statistics or CLT event counts, even though the former is a successful workflow and the latter contains the TWAP/continuity fixes. The corrected source is in commit `f64965d` plus the JSON-safe regression-fixture correction in `3b68d6f`; a new historical inventory run must complete against this code before any cohort-level conclusion is reported.

## 2026-11-20 price-only swing-break control

Using the public BingX 15-minute OHLC archive for 2026-10-03 through 2026-11-09, a price-only control was evaluated on BTC, ETH, SOL, HYPE and INJ. A signal is a close outside the prior eight completed candles' swing high/low; signals are separated by a 36-bar cooldown; entry is the next 15-minute open. The same 415 events were retained at every horizon so each trade had a complete nine-hour follow-up. Mean gross directional returns were +3.59 bps at 30m, +5.98 bps at 1h, +2.72 bps at 2h, +3.23 bps at 4h, and +0.95 bps at 9h. A reproducible three-day moving-block bootstrap over UTC dates (20,000 resamples, seed 20261120, the same sampled date blocks across markets) produced 95% intervals that crossed zero at every horizon: [-3.46, 12.11], [-1.35, 15.12], [-6.56, 13.24], [-9.50, 16.94], and [-19.72, 21.57] bps, respectively.

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


## 2026-11-20 reproducible price-control implementation

The pooled price-only baseline is now reproducible by `scripts/research/clt_price_control.py`, tested by `tests/research/test_clt_price_control.py`, and invoked from the public-market-data workflow after candle collection and cross-venue comparison. Defaults are eight prior candles for the swing, 36 bars of cooldown, next-candle-open entry, horizons of 2/4/8/16/36 bars, 20,000 three-day moving-block bootstrap resamples and fixed seed 20261120. All horizons use the same events by requiring complete 36-bar follow-up. The bootstrap samples calendar-date blocks shared across markets and calculates an event-weighted pooled mean. The JSON artifact records per-market counts, gross mean, pooled gross/net fee scenarios and confidence intervals. It is explicitly a price-only control, not a CLT result.


## 2026-11-20 per-market uncertainty and leave-one-market-out control

The price-only control artifact now emits event counts, gross means, maker/maker and taker/taker fee-adjusted means, three-day moving-block 95% intervals before fees, and intervals shifted by the same fixed round-trip fee assumptions for each market separately. It also emits the complete pooled summary excluding HYPE. All summaries use the same calendar date range, block length and random seed, so each resample uses the same sampled calendar blocks across the pooled, per-market and ex-HYPE views. These are descriptive multiple views of one archive, not five independent confirmatory tests. The JSON also keeps the original pooled fields for compatibility.


## 2026-11-21 inventory sample provenance and hard-page-cap guard

The historical inventory report now records a SHA-256 fingerprint of the sorted selected wallet-address set. It does not expose addresses and does not freeze the public leaderboard sample, but it makes it possible to tell whether two runs used the same cohort before comparing their aggregates. The collector also fails closed when it exhausts its full five-page budget with a full final page, even if de-duplication leaves fewer than 10,000 unique fill keys. This prevents a theoretically truncated history from being treated as complete merely because the local unique-key count fell below the API ceiling. Both behaviours have isolated regression tests.


## 2026-11-21 per-market price-control interpretation

The refreshed artifact from cross-venue market-data run `44813460457` (code head `0d729f8c`) contains 415 price-only swing-break events across the five markets. Event counts were BTC 83, ETH 80, SOL 83, HYPE 84 and INJ 85. The pooled maker/maker mean after the fixed 4 bps round-trip fee was -0.41, +1.98, -1.28, -0.77 and -3.05 bps at 30m, 1h, 2h, 4h and 9h. The pooled taker/taker mean after 10 bps was negative at every horizon (-6.41, -4.02, -7.28, -6.77 and -9.05 bps). The pooled 95% moving-block intervals crossed zero at every horizon.

The pooled result hides a strong market concentration. HYPE alone had maker/maker net means of +20.69, +32.20 and +32.41 bps at 1h, 2h and 4h; taker/taker means were +14.69, +26.20 and +26.41 bps. Its corresponding 95% block-bootstrap intervals remained above zero after both fee assumptions: maker/maker [10.38, 33.65], [18.54, 49.31], [10.39, 58.32] bps and taker/taker [4.38, 27.65], [12.54, 43.31], [4.39, 52.32] bps. HYPE's 30m and 9h intervals crossed zero. This is a candidate for a separate HYPE-only, strictly out-of-sample control, not evidence for CLT and not permission to promote the breakout rule.

Removing HYPE leaves 331 events and weakens the pooled gross means to +2.75, +1.23, -5.78, -5.18 and -1.26 bps across 30m, 1h, 2h, 4h and 9h. Under maker/maker fees these become -1.25, -2.77, -9.78, -9.18 and -5.26 bps; under taker/taker fees, -7.25, -8.77, -15.78, -15.18 and -11.26 bps. The ex-HYPE bootstrap intervals cross zero at all horizons before fees, except the 2h taker/taker interval, whose upper bound is still negative. That last result is a warning about the control's generality, not a CLT signal.

Market-level maker/maker net means (bps) were:

| Market | 30m | 1h | 2h | 4h | 9h |
|---|---:|---:|---:|---:|---:|
| BTC | -6.28 | -5.88 | -10.83 | -6.65 | +9.27 |
| ETH | -4.19 | -0.60 | +3.63 | +5.13 | +1.92 |
| SOL | +0.16 | -2.37 | +1.99 | -0.10 | -0.50 |
| HYPE | +2.88 | +20.69 | +32.20 | +32.41 | +5.62 |
| INJ | +5.06 | -2.18 | -32.88 | -34.00 | -30.84 |

These per-market estimates use the same 37-day archive and are exploratory multiple views. The fee model still excludes spread, slippage, market impact and funding; the account's actual commission tier is not known. The 84 HYPE events are not an independent holdout. Any follow-up must freeze the rule and evaluate a later, untouched period, report long and short directions separately, include realized funding timestamps and execution costs, and compare against the same-period no-signal baseline. Most importantly, this price-only control is a benchmark only: the CLT hypothesis still requires the corrected small/large wallet-inventory sequence and must not be inferred from candle breaks.


### HYPE direction split: exploratory diagnostic

A separate diagnostic split the same 84 HYPE price-only events by breakout direction, using the same 3-day moving-block bootstrap, 20,000 resamples and seed 20261120. There were 44 upside breaks and 40 downside breaks. At 1h/2h/4h, upside-break gross means were +29.00/+39.39/+44.56 bps; after the 10 bps taker/taker fee assumption they were +19.00/+29.39/+34.56 bps, with 95% intervals [+4.62,+38.18], [+6.77,+57.78], [+7.99,+74.35] bps. Downside-break gross means were +19.96/+32.70/+27.43 bps; after taker/taker fees they were +9.96/+22.70/+17.43 bps, but the corresponding intervals crossed zero at all three horizons: [-9.41,+30.12], [-2.27,+48.59], [-21.13,+60.31] bps. The long-side intervals remained positive after the maker/maker fee assumption as well.

This indicates that the apparent HYPE control edge is directionally asymmetric and is strongest after upside breaks. It could be a market-regime/trend effect rather than a crowd-liquidity mechanism. Treat this as a diagnostic computed from the archived CSV, not a separately registered hypothesis or holdout result. The next reproducible version of the price-control report should emit direction-split summaries automatically. A CLT result must beat this direction- and market-matched price-only baseline; otherwise the model is merely relabelling a basic breakout.


## 2026-11-21 source-signal / execution-venue correction

A methodology review found that the first price-only control implementation generated its swing-break signal from BingX candles, even though the intended CLT architecture reconstructs participant inventory and signal context on Hyperliquid, then executes on BingX. This was not a venue-matched baseline. The control now reads Hyperliquid 15m candles for prior swing levels and signal direction, and uses the exactly timestamp-aligned BingX next-open and exit-close prices for returns. It refuses any candle count or timestamp mismatch. The previous BingX-signal artifact and its HYPE directional diagnostics remain exploratory context only; they must not be used as the final benchmark against CLT. A fresh market-data workflow run is required to produce the corrected source-signal / execution-venue baseline.


The venue-aligned control also timestamps each signal at the completed 15m candle close, not at the candle's opening timestamp. This keeps UTC day-block bootstrap assignment correct for signals whose candle crosses midnight. The next BingX execution candle must open at that exact signal-close boundary; otherwise the event is rejected.


## 2026-11-21 corrected source-signal / execution-venue price control

The final cross-venue artifact is workflow run `44880346384`, code head `337bfee1`. It uses Hyperliquid 15m candles to form the eight-bar swing-break signal and timestamp-aligned BingX USDT-M 15m candles for next-open entry and exit marks. The signal is timestamped at the completed source candle close, which must equal the next BingX candle open. The archive spans 2026-10-03 through 2026-11-09; the five markets contain 419 events: BTC 83, ETH 82, SOL 84, HYPE 84 and INJ 86.

Pooled net mean returns (bps), with fixed round-trip fee assumptions, were:

| Horizon | Maker/maker | Taker/taker |
|---|---:|---:|
| 30m | +3.88 | -2.12 |
| 1h | +3.99 | -2.01 |
| 2h | +1.11 | -4.89 |
| 4h | +1.14 | -4.86 |
| 9h | -7.97 | -13.97 |

All pooled 95% three-day moving-block intervals cross zero before fees: [-0.02, 18.19], [-0.80, 19.33], [-7.84, 19.62], [-9.59, 20.88] and [-23.08, 12.60] bps. The corresponding taker/taker intervals are [-10.02, 8.19], [-10.80, 9.33], [-17.84, 9.62], [-19.59, 10.88] and [-33.08, 2.60] bps. Excluding HYPE leaves 335 events; maker/maker means are +3.82, +1.67, -2.32, -4.70 and -10.15 bps, while taker/taker means are -2.18, -4.33, -8.32, -10.70 and -16.15 bps.

HYPE remains the most interesting market, but the effect is narrower than in the earlier, incorrectly BingX-signalled control. Its 84 events split evenly into 42 upside and 42 downside breaks. For upside breaks, maker/maker means at 1h/2h/4h were +19.17/+27.26/+38.86 bps with block-bootstrap intervals [+1.51,+37.21], [+3.62,+51.22], [+4.77,+77.05]. Taker/taker means were +13.17/+21.26/+32.86 bps, but their intervals crossed zero at all three horizons: [-4.49,+31.21], [-2.38,+45.22], [-1.23,+71.05]. Downside-break intervals crossed zero under both fee assumptions at all three horizons. Thus the only apparently robust slice is HYPE upside under maker fees.

Critical execution caveat: the control enters at the next candle open, an immediate execution assumption. It cannot claim maker fees unless a resting-limit fill model proves those orders actually fill at the assumed price. Taker/taker is the more relevant conservative fee scenario for immediate execution, and even that excludes spread, slippage, market impact and funding. This short in-sample control therefore does not establish a robust executable price-only edge. It is a benchmark for the CLT event study, not a candidate promotion result.

The previous run `44813460457` generated its swing signal from BingX candles and is superseded as a source-venue comparator. Use run `44880346384` for the Hyperliquid-signal/BingX-execution baseline. All results remain exploratory, with no untouched out-of-sample period.


## 2026-11-21 direction-aware price-control output

The reproducible price-only control now reports LONG and SHORT separately for each market, pooled, and ex-HYPE. Each direction retains its own event count, gross/fee-adjusted means and date-block confidence intervals; the two sides are never pooled inside a directional view.

A 1.5x taker-fee-only sensitivity is emitted as a lower-bound fee stress (15 bps round trip using the published 5 bps/side base rate). It is **not** a full 1.5x execution-cost stress: spread, slippage, funding, market impact and the owner's actual fee tier remain unavailable. The report remains a price-only benchmark, not a CLT event study or promotion result.


## 2026-11-21 corrected Hyperliquid inventory feasibility result

Historical inventory workflow run `44879898930`, code head `c07a8722`, completed successfully. Its artifact covers 2026-10-03 00:00 UTC through 2026-11-09 00:00 UTC, with seven calibration days followed by the test phase. The selected 120-wallet leaderboard sample has SHA-256 fingerprint `83ad8df62510f41373b53eb10cccaee12bba5950b19a053323c3fc0733d467a4`; this is a cohort fingerprint, not a frozen public leaderboard manifest.

The collector returned 199,303 API fill rows and 10,911 TWAP slice rows over 186 API pages. It recovered same-millisecond page-boundary fills for 23 wallets and found zero unresolved page-boundary collisions or ambiguous same-timestamp groups. Position continuity was checked over 60,940 transitions: 13 failures (0.0213%) affected four wallet-market pairs. No fill had missing/invalid position fields and no direction label contradicted the reconstructed position math; one unrecognized direction label caused its wallet-market series to be excluded.

Coverage limits remain material. Seven wallets hit the 10,000-fill API ceiling and 13 hit the 2,000-row TWAP endpoint ceiling; these sets did not overlap, so 20 of 120 selected wallets were conservatively excluded from cohort construction. Eighty selected wallets had fills in at least one core market. The TWAP endpoint still returns a capped recent slice rather than a demonstrated complete historical TWAP interval, so the result cannot claim full TWAP coverage. No raw wallet addresses are written to the artifact.

Market calibration coverage was BTC 14 wallets (9 small / 5 large), ETH 7 (4 / 3), HYPE 10 (6 / 4), SOL 5 (3 / 2, below the six-wallet minimum and therefore not cohort-calibrated), and INJ 0. Only BTC, ETH and HYPE support the minimum cohort size. The sample remains selected from leaderboard monthly-volume deciles, not a representative retail/large-trader census; small/large labels mean relative position-notional size within this sample only.

In the test phase, a candidate inventory sequence was defined narrowly as at least three distinct small-cohort wallets adding net inventory in one direction during the preceding 12 completed 15-minute bars, followed by at least one large-cohort wallet adding net inventory in the opposite direction in the current bar. This produced:

| Market | Large SHORT-add buckets after small LONG crowd | Large LONG-add buckets after small SHORT crowd |
|---|---:|---:|
| BTC | 8 of 74 | 3 of 151 |
| ETH | 0 of 4 | 0 of 36 |
| HYPE | 0 of 22 | 0 of 51 |
| SOL | 0 of 10 | 0 of 17 |
| INJ | 0 of 0 | 0 of 0 |

All 11 qualifying buckets occurred in BTC. They were clustered in time: using a simple grouping rule that starts a new cluster only after a gap greater than 12 bars, the eight small-LONG/large-SHORT buckets form three clusters and the three small-SHORT/large-LONG buckets form two clusters. They are therefore not 11 independent events. The qualifying BTC buckets were 2026-10-14 14:00 and 15:00 UTC; 2026-11-04 17:30 UTC; 2026-11-05 15:30, 15:45, 17:30, 18:30 and 19:15 UTC for small-LONG then large-SHORT, and 2026-11-02 14:30 and 15:15 UTC plus 2026-11-04 23:45 UTC for small-SHORT then large-LONG.

Interpretation: the corrected sample does contain a small number of BTC inventory configurations consistent with the first two legs of the hypothesis. It does not show that large traders were absorbing retail liquidity, that a structural break followed, or that price then moved against the crowd. The evidence is sparse, BTC-concentrated and clustered; ETH/HYPE/SOL do not reproduce the pattern in this sample. This is neither a rejection nor validation of CLT, and it provides no profitability estimate.

The next decisive test is to join these inventory candidate windows to the Hyperliquid source-price series, label a pre-registered structural break and post-break reversal, then compare against matched non-crowd windows and the direction-aware Hyperliquid-signal/BingX-execution price-only baseline. TWAP pagination/coverage and the non-representative wallet sample must be improved or explicitly bounded before any broad market claim. Do not promote or merge the strategy based on this feasibility pilot.

## 2026-11-21 first complete source-price join: no valid CLT entries

The research branch added a read-only Hyperliquid 15m candle collector and fetched the same 2026-10-03 00:00 UTC to 2026-11-09 00:00 UTC window as the inventory and BingX archives. Workflow run `44888633685`, artifact `14701236791`, returned 3,552 candles for each of BTC, ETH, SOL, HYPE and INJ. Every series has exact 15-minute continuity, from 2026-10-03 00:00 UTC through 2026-11-08 23:45 UTC, with zero missing or duplicate intervals. Hyperliquid source candles and BingX execution candles align on the same 3,552 timestamps for BTC; the collector artifact passed its isolated tests and lint, and the repository quality-gates run on this revision passed.

The pre-existing inventory feasibility candidates were then joined to Hyperliquid source candles. A structure break was defined without tuning: within the next 12 completed 15m bars after the large opposite addition, a close must breach the prior eight completed Hyperliquid candles' low (after small-cohort LONG) or high (after small-cohort SHORT). The signal is observable only at that candle close; any hypothetical execution price is the next BingX 15m open. Continuation is checked strictly after the large-add bar, using net small-cohort inventory additions and distinct small-cohort wallets over those later bars. The required minimum remains three distinct small-cohort adders; it is not relaxed after seeing the result.

| Large opposite-add time (UTC) | Setup | Large net add (USD) | Break within 12 bars? | Distinct continued small adders |
|---|---|---:|---|---:|
| 2026-10-14 14:00 | Crowd LONG / large SHORT | 428,132 | Yes, 14:45 | 0 |
| 2026-10-14 15:00 | Crowd LONG / large SHORT | 30,145 | Yes, 16:00 | 0 |
| 2026-11-02 14:30 | Crowd SHORT / large LONG | 1,548 | No | — |
| 2026-11-02 15:15 | Crowd SHORT / large LONG | 554,688 | No | — |
| 2026-11-04 17:30 | Crowd LONG / large SHORT | 681,192 | Yes, 20:00 | 1 |
| 2026-11-04 23:45 | Crowd SHORT / large LONG | 3,752 | No | — |
| 2026-11-05 15:30 | Crowd LONG / large SHORT | 2,969 | Yes, 16:00 | 0 |
| 2026-11-05 15:45 | Crowd LONG / large SHORT | 1,809 | No | — |
| 2026-11-05 17:30 | Crowd LONG / large SHORT | 196,511 | No | — |
| 2026-11-05 18:30 | Crowd LONG / large SHORT | 237,610 | No | — |
| 2026-11-05 19:15 | Crowd LONG / large SHORT | 262,616 | No | — |

Result: 4 of 11 first-two-leg inventory candidates had the defined price break, but **0 of 11 completed the required post-large continuation by at least three distinct small-cohort wallets**. One of the four break cases had only one continued adder and net small-cohort inventory change of approximately -$15, i.e. not continued net accumulation. The other break cases had zero qualifying continued adders. Therefore the full causal CLT event model emits zero valid entries in this test archive, before any fee, spread, slippage or funding calculation.

For transparency only, the four incomplete break cases were marked to BingX next-open execution prices. Their gross direction-adjusted means were -31.47, -17.11, -23.63, -30.26 and -22.34 bps at 30m, 1h, 2h, 4h and 9h. These are **not CLT trades**: all four fail the mandatory continuation condition, two occur in the same Oct 14 cluster, and no inferential confidence interval or profit-factor estimate is justified. They must not be used as a substitute event sample.

### Verdict

The current strict CLT formulation is **not demonstrated profitable and is not operationally viable on this sample**: it produces zero eligible trades. This is not proof that the underlying market hypothesis can never work, and it is not a statistically valid loss verdict because there are no valid entries. It is a clear rejection of the claim that the available 2026-10-03 to 2026-11-08 sample already supports this exact sequence. Do not relax the three-wallet continuation gate, retune thresholds on this test period, or promote the strategy to Zeus. A future attempt requires a prospectively frozen cohort and a longer untouched sample; only then can PF, expectancy, drawdown and fully costed returns be evaluated.
