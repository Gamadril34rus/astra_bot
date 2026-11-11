# Quarter-hour boundary: isolated event-study protocol

**Status: IMPLEMENTATION ONLY — NO DATA RUN OR PROFITABILITY CLAIM.**

This experiment tests two competing explanations of a price impulse on UTC quarter-hour boundaries. It does not modify Zeus, production configuration, VST, risk controls, or order routing.

## Research question

At 00, 15, 30 and 45 UTC, does an unusually large first 5-minute candle tend to reverse or continue over the following 30 minutes to 4 hours?

This is deliberately a **5-minute candle proxy**, not a replication of the cited paper's first-10-second signed trade-flow measurement. The paper's result is a motivation to test the effect, not evidence that it transfers to BingX or these contracts.

## Frozen event definition

An event is eligible only when all conditions are known at the close of the first 5m candle:

1. The candle open timestamp is exactly on a UTC 15-minute boundary.
2. Absolute open-to-close return is between 0.30% and 1.50%, inclusive.
3. Its base volume is at least 1.5 times the median volume of the preceding 20 completed 5m candles.
4. A fully closed 4h candle is available. The trend is bullish only when EMA20 > EMA50 and close > EMA20; bearish only when EMA20 < EMA50 and close < EMA20. Neutral trend is excluded.
5. The next 5m candle exists. Entry is proxied at its open, never at the signal candle close.

No full 30m-bar volume, future 4h close, current L1 spread, or incomplete candle may enter the signal.

## Competing variants

- **REVERSAL:** trade opposite the first 5m impulse only when the last completed 4h trend opposes that impulse.
- **CONTINUATION:** trade with the first 5m impulse only when the last completed 4h trend confirms it.

No parameter grid or threshold tuning is permitted in this first test. Both variants use the same event definition, entry proxy, horizons and costs.

## Horizons and accounting

Fixed horizons are 6, 12, 24 and 48 completed 5m bars after entry (30m, 1h, 2h and 4h). This is an event study, not a stop/target strategy backtest.

The cost-only net return subtracts a 4 bps taker fee plus a 2 bps slippage proxy per side for BTC/ETH (12 bps round trip), and 5 bps slippage per side for SOL/HYPE/INJ (18 bps round trip). A 1.5x cost stress (18 / 27 bps round trip, respectively) is reported alongside the base proxy. Funding is deducted only when the downloaded funding history brackets the full entry-to-exit interval and has no detected large timestamp gap. Otherwise funded net return is **UNKNOWN**, not zero.

There is no historical BingX BBO/depth tape in the current repository. Consequently the cost proxy is not a verified fill model. No historical spread or fill probability is fabricated. No PF/DD/Sharpe claim is made for a portfolio strategy.

## Frozen calendar splits

| Split | UTC dates, inclusive |
|---|---|
| IS | 2025-07-09 through 2025-12-31 |
| Validation | 2026-01-01 through 2026-05-31 |
| OOS | 2026-06-01 through 2026-11-10 (the partial 2026-11-11 UTC day is excluded) |

All symbols use the same calendar boundaries. A symbol's unavailable pre-listing dates remain absent; they are not padded with synthetic candles. Each event is assigned to a split only if its complete forward outcome stays inside that split; cross-boundary outcomes are purged. The OOS period is evaluated once after the data archive and methodology are frozen.

## Data collection

The one-shot GitHub Actions workflow downloads 5m candles and historical funding from BingX public endpoints:

- `/openApi/swap/v3/quote/klines`
- `/openApi/swap/v2/quote/fundingRate`

It uses each symbol's first timestamp from the existing `research/positioning-data` 30m manifest, respects a conservative request rate, verifies contiguous 5m candles page-by-page, and saves SHA-256 hashes in `download_manifest.json`. The workflow uploads data and report as an artifact; it does not commit raw data to master or production branches.

If BingX does not return the requested history, the run must fail with a coverage error. It must not fill gaps, silently drop duplicates, or replace missing funding with a fixed assumed rate.

## Inference and reporting

Results are reported by split, variant, symbol and horizon. Pooled estimates include a 95% UTC-day cluster-bootstrap interval (2,000 resamples, seed 20261111) to reduce false precision from overlapping events and same-day cross-symbol dependence. Per-symbol sample counts and funding coverage are mandatory.

This stage is a screening experiment only. A positive event-study result is not sufficient to deploy a strategy. A later strategy test would still need a predeclared stop/exit model, mark-to-market equity, realistic exchange execution data, portfolio capital allocation, costs ×1.5, and a fresh untouched OOS period.

## Run

After the research PR is reviewed and merged, the master push trigger starts one data download and event-study run. The workflow also remains manually dispatchable. It uploads `quarter-hour-boundary-research` containing raw CSVs, SHA-256 manifest and `report.json`.

## Safety boundary

- Research branch only.
- No edits to `SETTINGS.md`, `zeus_*`, production configs or live journals.
- No VST arm, REAL mode, private API keys or orders.
- No automatic merge or promotion from a positive report.
- Negative, inconclusive and insufficient-sample results must be retained.
