# SAR video baseline on BingX 15m: final discovery result

**Date:** 2026-11-21  
**Verdict:** NOT PASSED under standard BingX taker execution  
**Scope:** research only; no production or Zeus changes

## Frozen rule

- Parabolic SAR: start 0.02, increment 0.02, maximum 0.20.
- No EMA, volume, ATR, session, trend or other filters.
- No take-profit. Exit uses the prior completed SAR point as the stop for the current 15m bar; gap-through exits at bar open, otherwise a touched stop fills at the stop price.
- A completed SAR trend flip is acted on at the next 15m open.
- The same fixed rule is tested on BTC-USDT, ETH-USDT and SOL-USDT perpetuals.
- Portfolio profiles are 1% risk / 3% max open risk, 1.5% / 6%, and 2% / 8%.

## Data coverage

The BingX public endpoint returned 9,600 bars, not the requested 36,000, for 15m history. The final archive covers approximately 100 days:

| Market | Bars | First UTC | Last UTC | Gaps |
|---|---:|---|---|---:|
| BTC-USDT | 9,600 | 2026-08-13 13:45 | 2026-11-21 13:30 | 0 |
| ETH-USDT | 9,599 | 2026-08-13 14:00 | 2026-11-21 13:30 | 0 |
| SOL-USDT | 9,599 | 2026-08-13 14:00 | 2026-11-21 13:30 | 0 |

Raw candles are included in the workflow artifact with SHA-256 checksums. The test is not a one-year backtest; the API history limit prevents that claim.

## Full 100-day replay, before funding

- 2,319 trades across the three markets using the corrected canonical PSAR implementation.
- Mean net return: **-27.81 bps per trade** under the 10 bps/side slippage stress assumption.
- Median net return: approximately **-46.7 bps**.
- Win rate: **21.47%**.
- Raw per-trade profit factor: **0.442**.
- Portfolio profit factor at 1% risk / 3% max open risk: **0.425**.

The earlier portfolio simulator printed about $3.17 terminal equity and -99.948% drawdown for the 1% / 3% profile. **These figures are withdrawn as exchange-realistic performance claims**: the simulator does not model maintenance margin, liquidation/bankruptcy prices or full margin availability, and it does not replay funding cashflows into portfolio equity. See the independent audit note. Do not use those ending balances to forecast live account outcomes.

| Market | Trades | Mean net bps | Raw PF |
|---|---:|---:|---:|
| BTC-USDT | 779 | -28.79 | 0.341 |
| ETH-USDT | 757 | -25.83 | 0.472 |
| SOL-USDT | 775 | -28.87 | 0.493 |

Both directions lose: long PF 0.438, short PF 0.445.

## Cost and edge decomposition

The frozen cost model is 5 bps taker fee per side plus 10 bps slippage per side: 30 bps round trip.

The mean direction-adjusted price-only move before fees and slippage was only **+2.19 bps** per trade (price-only PF 1.093). After the 10 bps/side slippage assumption, mean gross return was **-17.81 bps**; after taker fees, mean net was **-27.81 bps**. With the published 5 bps/side base taker fee and zero slippage, the corrected replay still averages **-7.81 bps/trade** (PF 0.776). This is not merely a position-sizing or high-slippage problem.

## Holding time

- Median: **165 minutes** (2h45m).
- 90th percentile: **330 minutes** (5h30m).
- **96.67%** of trades lasted between 30 minutes and 9 hours.
- Maximum: 1,095 minutes (18h15m).

The holding-time profile mostly fits the intended intraday window. The failure is expectancy after executable costs, not excessive trade duration.

## Archived funding adjustment

Funding was applied to the subset fully covered by the frozen BingX funding archive (workflow run `42692378936`). The archive ends at 2026-11-09 20:23 UTC, so later trades are excluded from this funding-adjusted subset.

- 2,052 trades were funding-adjusted; 267 later trades were excluded at the archive boundary.
- All trades inside the archive-eligible period had complete funding coverage.
- Mean funding contribution: approximately **-0.024 bps per trade**, effectively negligible.
- Funding-covered subset without funding: mean **-27.96 bps**, PF **0.416**.
- Same subset with funding: mean **-28.02 bps**, PF **0.416**; win rate **20.86%**.

Per-market funding-adjusted PF: BTC 0.312, ETH 0.454, SOL 0.466. Funding does not change the conclusion.

The funding adjustment is trade-level. The original portfolio sizing/equity profiles are reported pre-funding; this report does not claim a full portfolio rerun with settlement cashflows.

## Engine and execution caveat

The original replay was superseded after an audit found missing reversal clamps, strict instead of touch-inclusive reversal checks, and fixed-long initialization. The corrected engine now uses TA-Lib-compatible initialization and reversal clamps. A separate replay matched all 2,319 archived trades. The engine identifies itself as `v4_mtm_batch_entries_interval_aware` and retains its upstream `re-audit required` status. A separate replay matched all 2,311 archived trades on symbol, side, entry/exit timestamps and exit reason. The current test suite remains narrow; portfolio margin accounting is not fully validated. A fee-only sensitivity is decisive: with the published 0.05% BingX base taker fee per side and **zero slippage**, mean net is still -7.85 bps/trade and PF 0.775. The fixed 10 bps/side slippage is a stress assumption, not measured execution data. See `SAR_15M_INDEPENDENT_AUDIT_2026-11-21.md` for corrections and limits.

## Final decision

**Reject the fixed SAR video baseline for BingX USDT-M perpetuals.** It loses on BTC, ETH and SOL, in both long and short directions, under the documented cost model. Funding is negligible and does not rescue it. Increasing risk/leverage only accelerates equity destruction.

Do not merge this strategy into master or connect it to Zeus. Any future SAR variant must be treated as a new hypothesis with a separately frozen out-of-sample period; do not tune this 100-day discovery sample to manufacture a pass.

## Reproducibility

- Corrected workflow run: `44914714370` (success).
- Code revision: `0cec06fa10cfeaa08da29794a0b63f180926d88d`.
- Artifact: `14710849082`.
- Workflow tests, funding-adjustment tests and Ruff passed.
