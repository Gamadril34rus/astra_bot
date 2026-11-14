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

- Intended promotion cohort for the 2025-11-13 to 2026-11-13 price window: ETH, SOL, HYPE and INJ. A preliminary union of BingX funding artifacts shows continuous latest segments across the start of that window; the exact 2026-11-13 right edge is still pending the new merge workflow. BTC is reported separately: its latest uninterrupted funding segment starts 2025-12-06, leaving only about 341 days.
- The 2026-04-05 date was the start of the earlier 30m HYPE candle archive, not its funding history. The preliminary funding union finds HYPE's latest uninterrupted funding segment starts 2025-11-06; HYPE may join the four-symbol cohort only after exact funding right-edge and 15m/1h candle coverage are verified.
- Candle and funding archives must be from BingX swap endpoints, with recomputed SHA-256, zero candle gaps, and no funding imputation. The exact first/last timestamps and hashes are written to the run artifact.
- Chronological 60/20/20 IS/validation/OOS. Any trade crossing a split boundary is excluded, not assigned by entry time alone. Rules are frozen before OOS is read.
- Report gross return, taker fees, spread, slippage, funding and net return separately; report PF, expectancy, win rate, hold distribution, positive-month share, drawdown and concurrent exposure by cohort, family, symbol and side.

## Prior research and provenance corrections

- The four-family 30m router in the adaptive-perpetual artifact was negative: OOS PF 0.5406, net expectancy -0.24751% per trade, 74 OOS trades, and no positive OOS months. It is rejected, not a tuning target.
- The earlier 30m lab artifact from PR #344 run #102 reported OOS PF 0.563 (trend pullback), 0.692 (trend expansion) and 0.526 (range reversion); the corresponding stress PFs were 0.477, 0.590 and 0.438. No family passed.
- Important provenance correction: adaptive artifact run #92 (ID 42692378936) was executed on SHA `eb1864bb6020f32c2ad31cb7788622cd8b2694f0` on branch `research/confirmed-breakout-retest`, not on PR #343 HEAD `377c83af7851e681a2cd1ab8a9f36e33b1fa62e8`. PR #343 HEAD has no associated Actions run. The raw 30m/1h candle hashes in that artifact were independently recomputed and matched its manifest, and all listed candle gap counts were zero; however, that artifact contains no 15m candles. The new candidate workflow therefore downloads fresh 15m/1h swap candles and funding from BingX rather than treating run #92 as PR #343 CI evidence.
- Superseded by the preliminary 2026-11-13 union audit: BTC 2025-12-06; ETH 2025-07-09; SOL 2025-09-02; HYPE 2025-11-06; INJ 2025-07-09. ETH/SOL/HYPE/INJ are the intended 365-day funding cohort, subject to the pending exact right-edge gate. BTC is excluded until its funding gap is resolved.


### Run 34 evidence update (2026-11-10)

The funding-history overlap must not be confused with candle-history coverage. Run 34 (workflow run ID `42807247099`, candidate SHA `de8269414ef2eeee8da95a19f925bfd2f67cb2f8`) verified funding archives and candle SHA-256/gap checks, but the common 15m candle window for BTC/ETH/SOL/INJ was only 2026-08-02 13:15 UTC through 2026-11-10 11:15 UTC (99.92 days). The latest contiguous funding segments begin earlier; that does not establish 12 months of 15m price history. The archive workflow skipped empty leading BingX kline windows and retained only the available later candles. Do not describe this run as a 12-month 15m backtest.

- Four-symbol core, base costs: 37 completed trades; IS 29 trades, PF 0.9627, net expectancy -0.01705%; validation 8 trades, PF 0.6550, net expectancy -0.20481%; combined PF 0.8815, net expectancy -0.05765%.
- Four-symbol core, 1.5x costs: IS PF 0.8041, validation PF 0.5617, combined PF 0.7405 and net expectancy -0.14015%.
- HYPE's earlier 11-trade screen (8 IS, 3 validation) remains a small-sample diagnostic with negative validation expectancy in both cost cases. Its exclusion from the then-current core was based on the 30m candle archive start, not funding coverage; the preliminary funding union supports HYPE start-side coverage; the exact 365-day funding right edge is still pending. This does not establish 365 days of 15m candle coverage or a tradable edge.
- The IS/validation gate correctly failed, so no OOS results were generated. Candidate rejected for now; do not tune against OOS or register it in production.

The next research step is to validate the 365-day BingX-only 1m-to-15m/1h archive and run a new frozen 15m hypothesis against actual BingX candles and funding. No Binance, OKX or Hyperliquid candles are used as BingX fills or as candidate validation. No paid data source is authorized.

## Holdout protection

The workflow first publishes IS and validation only. The OOS summary is not generated unless both IS and validation have PF >=1.0 and positive net expectancy in both base and 1.5x cost cases. If this screen fails, the workflow stops with OOS unopened; the next hypothesis must be developed using IS/validation only. The intended 365-day funding cohort is ETH/SOL/HYPE/INJ, pending exact merged funding and candle coverage gates; BTC remains separate because its latest uninterrupted funding segment starts 2025-12-06.

## Historical gate

A candidate can only be recommended for a separate VST forward test if OOS net PF >=1.25, net expectancy >0, at least 100 combined OOS trades, at least 25 trades per side before side-specific claims, >=70% positive OOS calendar months, acceptable drawdown and positive net expectancy under 1.5x execution costs. Insufficient history or sample size is a failure to demonstrate an edge, not a pass.

OHLC fills, spread and slippage are research proxies, not exchange-confirmed fills. Historical success would not guarantee future profits. Any pass only authorizes owner review and VST planning.

## Safety

No production strategy registration, live Zeus execution, risk, leverage, sizing, production stops/exits, VST routing or REAL routing is changed. No PR merge or activation is authorized.



### 2026-11-11 follow-up: holdout isolation and multi-regime screen

The previous `--max-period VALIDATION` implementation filtered OOS trades from the report after simulating the complete candle series. Although it did not publish OOS metrics, OOS OHLC/funding observations were still read by the simulator. This was not a sufficiently strict untouched-holdout boundary.

The research runner now:
- calculates the common chronological boundaries from timestamps and coverage metadata first;
- clips 15m signal candles, closed 1h context candles, and funding observations to the selected screen's exclusive cutoff before feature calculation/simulation;
- uses pre-window candles only for indicator warm-up, and skips all entries before the requested period start;
- runs OOS-only replay flat from the OOS boundary with `--max-period OOS`, rather than simulating earlier trades and filtering them afterward;
- records the exact trade-start and exclusive signal-close cutoff, plus whether OOS bars were processed.

Regression tests cover the cutoff, pre-OOS signal suppression, and OOS-only flat start. This is a holdout-integrity correction, not a change to the strategy rules.

The earlier four-family router (compression breakout, false-break reversal, trend pullback, range reversion) remains a rejected diagnostic baseline, not an eligible portfolio. The new independent SESSION_VWAP_RECLAIM hypothesis is screened on closed 15m BingX USDT-M candles with the last closed 1h context. The intended cohort is ETH/SOL/HYPE/INJ, pending exact data gates; BTC is separate. Base costs are 5 bps taker fee + 1.25 bps spread + 2 bps slippage per side; stress is 1.5x each. No module is allowed to force a trade when no positive net edge is demonstrated.

A read-only public API probe tests BingX `historicalTrades` with `fromId`, 1m/5m/15m/30m/1h swap klines, and 15m mark-price klines around 2025-12-26. It uses no account key and does not treat mark-price candles as last-traded OHLCV. The probe's empirical response, not the parameter documentation alone, determines whether free older 15m trade-price history can be recovered.

OOS remains closed unless the common BingX 15m candle window itself covers at least 365 days and both IS and validation are net-positive at base and 1.5x costs. Funding coverage alone cannot satisfy this condition. If OOS is eventually opened, the promotion gate also checks PF >=1.25, positive net expectancy, at least 100 OOS trades, >=70% positive OOS calendar months, positive 1.5x stress expectancy/PF, holding-time bounds, and drawdown proxies. The current workflow uses provisional research-only drawdown guardrails of no worse than -10% realized equal-notional drawdown and -20% trade-sequence drawdown; these remain OHLC/realized-PnL proxies and do not model intratrade margin/liquidation.

This work is on the descendant research branch used by PR #347, based on the preserved PR #343 research branch. PR #343 remains closed and unmerged; its rejected 30m four-family results are not promoted or rewritten. No production code, VST routing, REAL routing, risk, leverage, or production exits are changed.

## 2026-11-11 research hardening

The native 15m archive used by Run 34 provided only 99.91 days of common last-traded-price swap candles for BTC/ETH/SOL/INJ. A separate 365-day BingX 1m source archive is now being aggregated into 15m/1h bars; it remains acquisition-only and is accepted only after complete-day, no-gap, duplicate and native-overlap validation. The OOS gate stays closed until that archive proves the full common 15m window. Do not infer 15m availability from 1h or funding coverage.

The adaptive four-family script had two analysis limitations that are now corrected on the PR #347 research branch:

- Its relative-volume baseline previously included the signal candle in the rolling 20-bar mean. It now divides current closed-candle volume by the mean of the prior 20 closed candles (volume.shift(1).rolling(20)).
- Its original choose_signal returned the first match in fixed priority order (compression, false-break, trend pullback, range reversion). The resulting by_family tables were router-selected cohorts, not independent module tests. choose_signals now emits every matching family, and --family-only runs each family independently by symbol and side. The original fixed-priority router remains available for diagnostic comparison.

The research router now supports --enabled-families and explicit NONE. The workflow first evaluates each family independently on IS and validation at base and 1.5x costs. A family is eligible for the research router only if it has at least 20 IS trades and 10 validation trades and positive net expectancy with PF >=1.0 in all four period/cost combinations. If no family qualifies, the filtered router is forced to NO_TRADE. This is a research-only allowlist and does not register or activate any production strategy.

The earlier seven-date 500-minute probe has been superseded by a full 365-complete-UTC-day BingX 1m archive attempt. Its only permitted use is exact OHLCV aggregation into 15m/1h bars; it does not authorize 1m/5m signals or minute trading. Every source minute must be present, unique, correctly aligned and from the same BingX USDT-M contract. The aggregated 15m/1h series must match native BingX bars exactly over the available overlap. No incomplete window may be silently filled.

The previous router's by_family values must not be treated as independent module evidence. The new independent reports supersede those breakdowns for future selection. The PR #347 research branch remains Draft and unmerged; no production, VST or REAL settings are changed.


### 2026-11-11 relative-volume unit correction

A code review found that the previous quote-volume fallback selected quote volume on positive rows and base volume on zero rows. Those quantities have different units, so a single missing/zero quote-volume observation could distort the rolling relative-volume ratio and therefore alter signal eligibility. The research feature path now selects quote volume only when it is complete and internally consistent across the symbol history; otherwise it uses base volume for the entire symbol history. It never mixes the two units row by row. Regression tests cover both the shared regime screen and the dedicated 15m pullback feature path. This is a data-integrity correction, not a strategy-rule change.


### 2026-11-11 candle-archive cooldown correction

A review of the live research workflow found that the candle archive used three 300-second waits for BingX HTTP-200 code 100410, while the funding archive already used bounded 5/30/60-second backoff. The candle archive now uses the same 5/30/60-second schedule (four total attempts), with a regression test asserting the exact wait sequence. This avoids a 15-minute stall for one page without removing cooldown handling or increasing request rate. The archive remains sequential at 0.2 requests/second.


## 2026-11-13 BingX-only data and strategy extension

The prior funding summary mixed up the start of the older 30m HYPE candle archive with the start of its funding history, and overstated the BTC funding window. A preliminary offline union audit uses two independently downloaded BingX funding archives: base run 43213211531 and supplemental run 43424403539. Duplicate timestamps must carry identical rates; no funding is imputed. A new workflow is re-downloading the right edge and will be the authoritative coverage gate.

The latest uninterrupted funding segments in that union start at BTC 2025-12-06, ETH 2025-07-09, SOL 2025-09-02, HYPE 2025-11-06, and INJ 2025-07-09. Therefore ETH/SOL/HYPE/INJ are the intended 365-day funding cohort from 2025-11-13T00:00:00Z through 2026-11-13T00:00:00Z, subject to the pending exact right-edge verification. BTC has only about 341 days of uninterrupted coverage and is excluded from that promotion cohort, but remains available for a separate diagnostic.

A new merge workflow re-downloads BingX funding through 2026-11-14 and verifies source CSV SHA-256, duplicate-rate equality, each symbol's recent settlement cadence, the exact requested window edges, and the 365-day common cohort. The earlier 12-month refresh run 43424403539 failed its all-symbol strict check because of gaps outside the selected core window; that failure is not treated as complete coverage. The new merged artifact must pass its own gate before the strategy workflow can use it.

The 1m archive is acquisition-only. It aggregates actual BingX USDT-M 1m candles to 15m and 1h, with no 1m/5m signal trading. Its manifest explicitly marks quote volume, trade count, taker-buy base/quote volume, historical order book, historical open interest and wallet identity as unavailable. The strategy therefore uses base-asset volume only for its session VWAP and relative-volume baseline; it makes no order-flow or participant-identity claim.

A read-only check of the separate research/positioning-data branch at commit 2b9f1155d359ea1f4ce322091ed17686c8766bdf found blank taker-buy fields in the 30m candles and records=0, skipped_missing_flow=5 in the positioning shadow summary. The marker .positioning_taker_buy_backfill_v1 is not proof that those fields were populated. Workflow run 43426136989 also failed because a 30m candle captured about 24 seconds after close changed on re-fetch; that data path is not used for the 15m candidate.

A distinct research-only candidate, SESSION_VWAP_RECLAIM, has been added in scripts/research_bingx_session_vwap.py. It uses a closed 15m UTC-session VWAP excursion/reclaim, a closed 1h ADX range filter, next-15m-open entry, a three-bar swing stop, fixed signal-time VWAP target, 30m/90m no-progress exits and a hard 9h maximum. It is screened independently from the retired four-family router. IS and validation are simulated as separate flat periods; OOS is not simulated unless both periods pass at base and 1.5x execution costs. The existing research drawdown guardrails remain -10% realized equal-notional and -20% trade-sequence drawdown.

Current status: candidate code and regression tests are on the PR #347 descendant research branch; no historical candidate result exists yet. The 365-day 1m archive and merged funding artifact must both complete and pass their data gates before OOS can be opened. This is not a strategy recommendation, not VST approval, and not production activation.
