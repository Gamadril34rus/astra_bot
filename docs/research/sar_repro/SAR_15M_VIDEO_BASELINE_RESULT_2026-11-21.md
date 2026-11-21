# SAR video baseline on BingX 15m: corrected discovery result

**Date:** 2026-11-21  
**Verdict:** NOT PASSED under standard BingX taker execution  
**Scope:** research only; no production or Zeus changes

## Strategy rule tested

- Parabolic SAR: acceleration start 0.02, step 0.02, maximum 0.20.
- No EMA, volume, ATR, session or other filters.
- No take-profit.
- A completed SAR flip is acted on at the next 15m open.
- The stop for the current bar is the SAR value from the prior completed bar. Gap-through stops fill at bar open; otherwise a touched stop fills at the stop price.
- Markets: BTC-USDT, ETH-USDT and SOL-USDT perpetuals.

## Data

The public BingX endpoint returned approximately 9,600 15m candles per market, not the requested 36,000. Final scored archive:

| Market | Bars | First UTC | Last candle open UTC | Gaps |
|---|---:|---|---|---:|
| BTC-USDT | 9,602 | 2026-08-13 14:00 | 2026-11-21 14:15 | 0 |
| ETH-USDT | 9,602 | 2026-08-13 14:00 | 2026-11-21 14:15 | 0 |
| SOL-USDT | 9,602 | 2026-08-13 14:00 | 2026-11-21 14:15 | 0 |

The raw candles are frozen in the workflow artifact with SHA-256 checksums. This is about 100 days of discovery data, not a one-year backtest and not an untouched out-of-sample test.

## Important correction to the first replay

The first implementation omitted the standard reversal clamp to the previous/current candle range, used strict crossing rather than touch-inclusive reversal, and forced long as the initial trend. Those differences can change the SAR path and trades. That first 2,311-trade replay is superseded.

The corrected engine now uses TA-Lib-compatible initial direction inference and reversal clamps. Tests cover both reversal clamps, equal-price touch and initial-direction inference. A separate replay matched all **2,319** workflow trades on symbol, side, entry/exit timestamps and exit reason.

See `SAR_15M_INDEPENDENT_AUDIT_2026-11-21.md` for the detailed correction record.

## Corrected trade results

From entry/exit mid-prices before costs:

- Trades: **2,319**
- Mean: **+2.1931 bps/trade**
- Median: **-16.8433 bps**
- Win rate: **35.23%**
- Price-only PF: **1.0781**

With the published BingX base perpetual taker fee of **0.05% per side** and **zero slippage**:

- Mean net: **-7.8068 bps/trade**
- Median: **-26.8349 bps**
- Win rate: **29.24%**
- PF: **0.7760**

This is the decisive sensitivity. The next-open entries and triggered stop exits are taker-like. The price-only edge does not cover even the base taker fee when slippage is set to zero.

With the original stress assumption of 10 bps slippage per side plus 5 bps taker fee per side:

- Mean net: **-27.8062 bps/trade**
- Median: **-46.8199 bps**
- Win rate: **21.47%**
- PF: **0.4424**

The 10 bps/side slippage is a stress assumption, not measured market-impact data. It is not required to make the baseline fail.

| Market | Trades | Mean after stress costs | PF after stress costs |
|---|---:|---:|---:|
| BTC-USDT | 780 | -28.55 bps | 0.345 |
| ETH-USDT | 762 | -26.00 bps | 0.470 |
| SOL-USDT | 777 | -28.82 bps | 0.494 |

Both directions lose under the stress model: long mean **-28.06 bps / PF 0.437**; short mean **-27.55 bps / PF 0.447**.

Official BingX fee schedule: https://bingxservice.zendesk.com/hc/en-001/articles/11263240298255-Perpetual-Futures-Fee-Schedule

## Holding time

- Median: **165 minutes** (2h45m).
- 90th percentile: **330 minutes** (5h30m).
- **96.55%** of trades lasted between 30 minutes and 9 hours.
- Maximum: 1,095 minutes (18h15m).

The holding-time distribution mostly fits the intended intraday window.

## Funding

Funding was applied to the subset fully covered by the frozen BingX funding archive:

- Covered: **2,052 / 2,319 trades (88.49%)**.
- Excluded after archive boundary: **267 trades**.
- Covered subset mean without funding: **-27.96 bps**.
- Covered subset mean with funding: **-28.02 bps**.
- Covered subset PF with funding: **0.4165**.
- Mean funding contribution on the covered subset: about **-0.061 bps/trade**.

The archive's last settlement timestamp is 2026-11-09 16:00 UTC. Funding does not change the conclusion, but the entire portfolio equity curve was not rerun with funding cashflows.

## Portfolio simulator limitation

The earlier portfolio simulator's ending equity and drawdown are **withdrawn as exchange-realistic performance claims**. It does not model maintenance-margin tiers, liquidation/bankruptcy prices or full margin-availability checks. Funding cashflows are not replayed through portfolio equity, and intrabar stop timing is approximated at candle-close timestamps for the MTM curve.

Per-trade expectancy and PF are separate from that compounding model and are reproducible under the stated fill/fee assumptions. Do not use the simulated ending balance or drawdown as a live-account forecast.

## Final decision

**Reject the unfiltered video baseline, SAR 0.02 / 0.02 / 0.20, for BingX USDT-M perpetuals under standard taker execution.** The corrected implementation still has only a small positive pre-cost price move, insufficient to cover base taker fees even with zero slippage.

This verdict is limited to the corrected 100-day discovery sample and these three markets. It is not a claim that every possible SAR variant is unprofitable, and it is not a direct reproduction of the video's XAUUSD market.

No parameter optimization was performed. No master merge, Zeus integration, or live changes.

## Reproducibility

- Corrected workflow run: `44914714370` (success).
- Code revision: `0cec06fa10cfeaa08da29794a0b63f180926d88d`.
- Artifact: `14710849082`.
- SAR tests, funding tests and Ruff: passed.
