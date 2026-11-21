# SAR 15m baseline: independent audit and corrected replay

**Date:** 2026-11-21  
**Status:** corrected canonical replay completed; unfiltered baseline rejected under BingX standard taker fees.  
**Scope:** BingX public 15m candles for BTC-USDT, ETH-USDT and SOL-USDT, workflow artifact `14710849082`, code `0cec06fa10cfeaa08da29794a0b63f180926d88d`.

## 1. Defect found and corrected

The first SAR replay implementation was not fully equivalent to the standard Wilder / TA-Lib state machine:

1. On a reversal it set SAR to the previous extreme point but omitted the mandatory clamp to the previous and current candle range.
2. It used strict crossing (`<` / `>`) instead of touch-inclusive reversal (`<=` / `>=`).
3. It forced the initial trend long by default instead of inferring the initial direction from the first two bars.

These differences can change the SAR path and trade sequence. The earlier 2,311-trade replay is superseded and must not be treated as the canonical result.

The engine was corrected to use TA-Lib-compatible initialization and reversal clamps. Explicit forced initial direction remains available only for sensitivity checks. Tests were added for both reversal clamps, equal-price touch, and initial-direction inference.

Reference implementation: TA-Lib SAR source code, including touch-inclusive reversal and reversal-range clamps: https://github.com/TA-Lib/ta-lib/blob/main/src/ta_func/ta_SAR.c

## 2. Corrected archive validation and independent replay

The corrected workflow fetched 9,602 candles per market before the final in-progress candle was removed from scoring. Final scored coverage:

- BTC-USDT: 9,602 bars
- ETH-USDT: 9,602 bars
- SOL-USDT: 9,602 bars
- Start: 2026-08-13 14:00 UTC
- Last candle open: 2026-11-21 14:15 UTC
- No duplicate timestamps, missing 15m intervals or invalid OHLC rows.

A separate replay of the corrected state machine matched all **2,319 trades** in the workflow artifact on symbol, side, entry timestamp, exit timestamp and exit reason. Mean net return matched to floating-point precision: **-27.8062 bps/trade** under the original stress model.

## 3. Corrected expectancy and cost sensitivity

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

With the published 0.02% maker fee on both sides and zero slippage, a deliberately optimistic sensitivity gives mean **-1.8068 bps/trade**, PF **0.9412**. This is not the expected execution route: next-open market entries and triggered stop exits are taker-like, and maker fills are not guaranteed.

With the original 10 bps/side slippage stress assumption plus 5 bps/side taker fee:

- Mean net: **-27.8062 bps/trade**
- Median: **-46.8199 bps**
- Win rate: **21.47%**
- PF: **0.4424**

Break-even is only about **1.097 bps fee per side** with zero slippage, or **1.097 bps slippage per side** with zero fees. The negative expectancy therefore does not depend solely on the arbitrary 10 bps/side slippage assumption: it remains negative under the published base taker fee after slippage is removed.

Official BingX fee schedule: https://bingxservice.zendesk.com/hc/en-001/articles/11263240298255-Perpetual-Futures-Fee-Schedule

Per-market price-only mean / PF and zero-slippage taker mean:

| Market | Trades | Price-only mean | Price-only PF | Taker fee only mean |
|---|---:|---:|---:|---:|
| BTC-USDT | 780 | +1.45 bps | 1.068 | -8.55 bps |
| ETH-USDT | 762 | +3.99 bps | 1.146 | -6.01 bps |
| SOL-USDT | 777 | +1.18 bps | 1.033 | -8.82 bps |

All three markets lose after the standard taker fee even with zero slippage.

## 4. Funding

Funding was applied to the subset fully covered by the frozen BingX funding archive:

- Covered: **2,052 / 2,319 trades (88.49%)**
- Excluded after archive boundary: 267 trades
- Covered subset mean without funding: **-27.96 bps**
- Covered subset mean with funding: **-28.02 bps**
- Covered subset PF with funding: **0.4165**

Funding is small and does not change the conclusion. The original portfolio equity curve was not rerun with funding cashflows.

## 5. Portfolio-simulation limitation

The earlier portfolio simulator's terminal equity and drawdown are **not approved as exchange-realistic performance claims**. It does not model maintenance-margin tiers, liquidation/bankruptcy prices, or full margin-availability checks; funding cashflows are not replayed through portfolio equity; intrabar stop timing is approximated at candle-close timestamps for the MTM curve. Do not use its printed terminal balance or drawdown as a live-account forecast.

Per-trade expectancy and PF are separate from that compounding model and are reproducible under the stated fill/fee assumptions.

## 6. Final decision

**Reject the unfiltered video baseline, SAR 0.02 / 0.02 / 0.20 on BingX USDT-M perpetuals, under standard taker execution.** The corrected canonical implementation still produces a small positive pre-cost price move that is insufficient to cover even base taker fees with zero slippage.

This conclusion is limited to the corrected 100-day discovery sample and these three symbols. There is no untouched out-of-sample period, and this is a crypto-perpetual analogue of a video example shown on XAUUSD. It is not a claim that every SAR variant is unprofitable.

No parameter optimization was performed. No master merge, Zeus integration, or live changes.
