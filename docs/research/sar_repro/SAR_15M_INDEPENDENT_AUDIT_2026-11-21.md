# SAR 15m baseline: independent audit corrections

**Date:** 2026-11-21  
**Scope:** audit of workflow artifact `14709825927`, code `97bbd7411b0fed2071e8766ebec9bda0a52cfc98`.  
**Status:** strategy baseline rejected on per-trade expectancy under standard BingX taker fees; portfolio equity curve is not approved for use.

## 1. Independent artifact checks

The three archived candle files were independently checked:

- BTC: 9,600 rows; ETH: 9,599; SOL: 9,599.
- No duplicate timestamps, missing 15m intervals, invalid OHLC rows, or non-positive prices.
- All three end at 2026-11-21 13:30 UTC. The archive spans approximately 100 days, not one year.

A separate replay implementation of the documented SAR state machine produced exactly 2,311 trades and matched the archived trade sequence on symbol, side, entry timestamp, exit timestamp and exit reason. The mean net return matched to floating-point precision: **-27.8456 bps/trade** under the frozen 5 bps/side taker fee plus 10 bps/side slippage model.

This confirms reproducibility of the current rule implementation, not that every modeling choice is correct.

## 2. Corrected price-only and fee sensitivity

The previous result document reported price-only mean **+2.59 bps**. Recalculation directly from the archived entry and exit mid-prices gives **+2.1538 bps/trade**, price-only PF **1.0767**, median **-16.79 bps**, and win rate **35.31%**. The +2.59 bps figure is withdrawn.

Applying the official base BingX Perpetual Futures taker rate of 0.05% per side, with **zero slippage**, gives:

- Mean net: **-7.8461 bps/trade**.
- PF: **0.7749**.
- Win rate: **29.29%**.

At the published 0.02% maker rate on both sides, also with zero slippage, the mechanical sensitivity is mean **-1.8462 bps/trade** and PF **0.9399**. This is an optimistic sensitivity only: the tested next-open entry and stop execution are market/taker-like, not guaranteed maker fills.

The mean edge reaches break-even only below approximately **1.077 bps fee per side** if slippage is zero, or below approximately **1.077 bps slippage per side** if fees are zero. Thus the negative expectancy is not caused solely by the arbitrary 10 bps/side slippage assumption. It remains negative under the published base taker fee even with slippage removed.

Official fee reference: BingX Perpetual Futures Fee Schedule, which lists 0.05% taker and 0.02% maker for the base tier: https://bingxservice.zendesk.com/hc/en-001/articles/11263240298255-Perpetual-Futures-Fee-Schedule

## 3. Important portfolio-simulation defect / limitation

The prior report's compounded portfolio ending balance ($3.24 from $5,000) and -99.947% drawdown must **not** be treated as a verified live-account outcome.

The portfolio simulator sizes notional from stop distance and a fixed 30 bps cost buffer, but it does not implement exchange margin tiers, maintenance margin, liquidation/bankruptcy prices, or a full margin-availability check. It also does not replay funding cashflows through the portfolio equity curve. Its MTM curve marks on 15m closes, while intrabar stop execution time is only approximated by the candle close timestamp. These limitations make the final balance and drawdown unsuitable as an exchange-realistic portfolio result.

The per-trade return and PF calculations are separate from that compounding model and remain reproducible for the stated fill/fee assumptions.

## 4. Remaining limitations

- Approximately 100 days of history only; no untouched out-of-sample period.
- The strategy is a crypto-perpetual analogue of a video example shown on XAUUSD, not a direct XAUUSD reproduction.
- The 10 bps/side slippage setting is a stress assumption, not measured market-impact data.
- Funding was adjusted on 2,046/2,311 trades (88.53%); the remaining 265 are beyond the funding archive boundary. Funding contribution on the covered subset is negligible, but portfolio-level funding compounding was not rerun.
- The source engine still labels itself `re-audit required`. Existing unit tests are narrow and do not constitute full validation of portfolio margin accounting.

## 5. Corrected decision

**Reject this unfiltered SAR 0.02/0.02/0.20 15m baseline for BingX USDT-M perpetuals under standard taker execution.** The observed price-only edge is too small to cover even the published base taker fee with zero slippage. The conclusion is specific to this 100-day sample, three symbols, and the fixed video rule. It is not a claim that every possible SAR variant is unprofitable.

Do not use the prior $3.24 terminal equity or -99.947% drawdown as a verified portfolio forecast. Do not connect this experiment to Zeus or merge it to master.
