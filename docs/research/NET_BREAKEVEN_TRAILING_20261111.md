# Net break-even + 0.5% trailing: preliminary replay (2026-11-11)

## Status

RESEARCH ONLY / NOT APPROVED FOR PRODUCTION. No production code, SETTINGS.md, live loop, VST/REAL flags, or exchange orders were changed.

This is a preliminary falsification pass, not a validated backtest. The archived market data is 30m OHLC, while Zeus evaluates exits approximately every five minutes. The OHLC bars cannot reveal the actual intrabar order of high/low touches or exact stop execution.

## Existing live exit behavior

The current live clock has two distinct mechanisms:
- sync_partial_breakeven moves the stop to net break-even after a favorable MFE threshold, not at the instant price merely covers costs. With session profiles enabled, the threshold is 0.30R (thin/off), 0.40R (normal), or 0.50R (fat).
- TP1 is still a separate partial take-profit. Session profiles set TP1 at 0.45R (thin/off), 0.55R (normal), or 0.70R (fat). The live protection order is configured for a 30% TP1 leg. The scale-out repair path can also market-reduce 30% when the exchange TP is still far away.

Therefore the user's proposal is not just “turn on BE”: it changes the BE activation condition from a positive R threshold to net-cost coverage, removes the 30% TP1 reduction, and adds a fixed-price-distance trailing rule after BE.

## Question

Test the requested exit idea on existing Zeus demo entries:
1. Keep the full position; do not reduce 30% at the early TP.
2. Once price reaches a net break-even threshold, move the stop to a level intended to cover entry/exit fees, observed entry slippage, estimated exit slippage, and funding accrued to that point.
3. Thereafter trail behind the best favorable price by a fixed distance, including 0.5%.
4. Compare with recorded realized demo outcomes on the same eligible trades.

## Data used

- 30m OHLCV archive on research/positioning-data: BTC, ETH, SOL, HYPE, INJ.
- Funding archives on the same branch: funding_SYMBOLUSDT.csv.
- Current demo trade journal on master: models/zeus_live_trades.jsonl.
- Market data end: 2026-11-11 10:00 UTC.
- 5m candles and tick/L1 execution tape were not available in the inspected archive.

## Preliminary sample

There were 121 recorded entry events for the five symbols in the current trade journal. Closed trade rows could be joined by signal_id/trade_id, but only 47 closed trades had at least one complete 30m candle strictly after entry and strictly before the recorded close time. The other 52 closed trades were excluded from this OHLC replay because the archive could not provide a complete post-entry bar before their close (or join/coverage was insufficient). Open/unmatched entries were not scored.

The 47-trade subset is small and selected toward positions that remained open long enough to observe a complete 30m candle. It is not representative proof of the entire strategy.

## Recorded result on the 47-trade subset

- realized PnL field: +1,789.68 USDT
- recorded fees: 1,759.38 USDT
- recorded funding fee field: -16.16 USDT (signed)
- simple net as PnL minus fees minus funding field: +46.45 USDT
- wins/losses by this simple net: 23 / 24

This baseline itself is only a journal-level calculation. It is not yet a complete order/fill-level reconstruction.

## Candidate replay: preliminary warning only

A prototype full-size replay of net-BE activation followed by a 0.5% trail, using 0.04% taker fee per side, observed entry slippage, archived funding, and modeled exit slippage, produced a negative net result on this restricted sample.

A sensitivity pass for the same 0.5% prototype estimated:
- zero extra exit slippage: -1,383.89 USDT
- 0.01% exit slippage: -1,562.71 USDT
- 0.02% exit slippage: -1,741.54 USDT
- 0.05% exit slippage: -2,278.00 USDT

These are not execution-grade figures. A separate coarse parameter-grid prototype produced inconsistent values for some trail distances, so the trail-distance ranking is deliberately withheld rather than presenting unstable calculations as a result. The 0.5% candidate did not beat recorded net PnL in the prototype, but the magnitude is not reliable enough for a final rejection.

## Why this is not a final verdict

1. 30m bars are too coarse for a 5m exit controller. A candle's high and low do not identify which price was visited first. Conservative stop-first ordering can materially change results.
2. No exact 5m/tick price path or L1 spread history. Exit slippage is an assumption, not measured execution.
3. Current realized PnL includes existing partial exits. The proposed alternative keeps 100% size. The comparison is not perfectly matched unless each income/fill event is reconstructed by order and quantity.
4. Recorded exit prices are often null / exchange-income reconciled. This prevents exact trade-by-trade price reconstruction.
5. The sample is selected and small (47). 52 closed trades could not be replayed using complete post-entry 30m bars.
6. Funding is approximated at initial notional. Exact position notional at every funding timestamp and partial-fill changes are not reconstructed.
7. The net-BE trigger must be calibrated against the exchange's actual fee tier, contract rounding, stop-market execution and slippage. A stop price equal to theoretical net-BE does not guarantee zero realized PnL.
8. The first prototype is not yet a checked-in reproducible test harness. Its output is a screening signal only.

## Preliminary conclusion

The current evidence does not justify replacing Zeus exits with a 0.5% trailing stop. The restricted prototype is negative after modeled costs, but the test is not sufficiently accurate to declare the idea rejected. The correct status is INCONCLUSIVE / DO NOT DEPLOY.

## Required next research step

Build a 5m (preferably 1m) historical archive for the five symbols and reconcile each demo trade from exchange fills/income:
- exact entry/exit fills and quantities;
- partial TP fills, stop replacements, and realized PnL;
- fee and funding ledger rows;
- mark/last price distinction and stop trigger basis.

Then replay the same exact entries under:
A. current Zeus exit policy;
B. full-size net-BE + 0.5% trailing;
C. full-size net-BE + 0.25/0.75/1.0% trailing;
D. full-size net-BE + existing ATR trailing.

Use adverse intrabar ordering where 1m still leaves ambiguity, and report per-symbol, long/short, fees, funding, slippage, equity DD, PF, expectancy, and trade count. No production changes until this passes.

## Guardrails

- Research branch only.
- No changes to SETTINGS.md, Zeus production code, live arming, risk limits, or REAL flags.
- No exchange orders.
- No claim of profitability based on this preliminary replay.
