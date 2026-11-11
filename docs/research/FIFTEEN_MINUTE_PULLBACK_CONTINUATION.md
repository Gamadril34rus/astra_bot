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


### Run 34 evidence update (2026-11-10)

The funding-history overlap must not be confused with candle-history coverage. Run 34 (workflow run ID `42807247099`, candidate SHA `de8269414ef2eeee8da95a19f925bfd2f67cb2f8`) verified funding archives and candle SHA-256/gap checks, but the common 15m candle window for BTC/ETH/SOL/INJ was only 2026-08-02 13:15 UTC through 2026-11-10 11:15 UTC (99.92 days). The latest contiguous funding segments begin earlier; that does not establish 12 months of 15m price history. The archive workflow skipped empty leading BingX kline windows and retained only the available later candles. Do not describe this run as a 12-month 15m backtest.

- Four-symbol core, base costs: 37 completed trades; IS 29 trades, PF 0.9627, net expectancy -0.01705%; validation 8 trades, PF 0.6550, net expectancy -0.20481%; combined PF 0.8815, net expectancy -0.05765%.
- Four-symbol core, 1.5x costs: IS PF 0.8041, validation PF 0.5617, combined PF 0.7405 and net expectancy -0.14015%.
- HYPE was screened separately and is not part of the core cohort: only 11 completed trades (8 IS, 3 validation); validation expectancy was negative in both cost cases. Its small sample and sub-12-month funding segment prevent any promotion claim.
- The IS/validation gate correctly failed, so no OOS results were generated. Candidate rejected for now; do not tune against OOS or register it in production.

The next research step is to obtain a longer, independently verifiable 15m perpetual-futures history without mislabelling another exchange's data as BingX. A non-BingX dataset may be used for hypothesis discovery only; any candidate must still be frozen and checked against BingX data and costs before it can be considered for VST planning. No paid data source is authorized.

## Holdout protection

The workflow first publishes IS and validation only. The OOS summary is not generated unless both IS and validation have PF >=1.0 and positive net expectancy in both base and 1.5x cost cases. If this screen fails, the workflow stops with OOS unopened; the next hypothesis must be developed using IS/validation only. HYPE is screened separately and does not enter the four-symbol OOS promotion cohort because its uninterrupted funding history is shorter than 12 months.

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

The next IS/validation screen runs the existing four-family regime router on 15m BingX USDT-M candles with the last closed 1h context: compression breakout, false-break reversal, trend pullback, and range reversion. BTC/ETH/SOL/INJ are evaluated as the core cohort; HYPE remains separate. Base costs are 5 bps taker fee + 1.25 bps spread + 2 bps slippage per side; stress is 1.5x each. No family is allowed to force a trade when no setup is present.

A read-only public API probe tests BingX `historicalTrades` with `fromId`, 1m/5m/15m/30m/1h swap klines, and 15m mark-price klines around 2025-12-26. It uses no account key and does not treat mark-price candles as last-traded OHLCV. The probe's empirical response, not the parameter documentation alone, determines whether free older 15m trade-price history can be recovered.

OOS remains closed unless the common BingX 15m candle window itself covers at least 365 days and both IS and validation are net-positive at base and 1.5x costs. Funding coverage alone cannot satisfy this condition. If OOS is eventually opened, the promotion gate also checks PF >=1.25, positive net expectancy, at least 100 OOS trades, >=70% positive OOS calendar months, positive 1.5x stress expectancy/PF, holding-time bounds, and drawdown proxies. The current workflow uses provisional research-only drawdown guardrails of no worse than -10% realized equal-notional drawdown and -20% trade-sequence drawdown; these remain OHLC/realized-PnL proxies and do not model intratrade margin/liquidation.

This work is on the descendant research branch used by PR #347, based on the preserved PR #343 research branch. PR #343 remains closed and unmerged; its rejected 30m four-family results are not promoted or rewritten. No production code, VST routing, REAL routing, risk, leverage, or production exits are changed.
