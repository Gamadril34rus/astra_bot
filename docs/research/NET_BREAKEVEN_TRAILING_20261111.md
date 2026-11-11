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


## Follow-up source audit: concrete live-code mismatches (2026-11-11)

This section records source-level findings from the current `master` snapshot. These are code-path facts, not claims that a particular live trade has already hit each path.

### 1. The early scale-out is an independent 30% market reduction

`scripts/zeus_live_clock.py::scale_out_early_if_due`:
- reads `ZEUS_TP1_RR` (default 0.6; session profile writes 0.45/0.55/0.70);
- when mark MFE reaches that R threshold and the stored TP is far (RR >= 1.5), submits a MARKET order for `tp_frac` (default 0.3);
- records `early_scaled=true` and installs a runner TP at 1.5R.

It is invoked from the live clock every tick. Therefore full-size holding is explicitly not the current behavior. The call path must be gated/removed in a separately approved change, not merely by adjusting the BE threshold.

### 2. The normal BE path is gated by MFE in R before net-BE is calculated

`sync_partial_breakeven` computes `mfe_r` from `initial_stop` and returns until `mfe_r >= trigger_r`. The live session profile controls this at 0.30R / 0.40R / 0.50R. Net-BE is calculated only after this gate. This does not implement activation as soon as net costs are covered.

### 3. The deferred partial-fill recovery path calculates net BE, then places the stop at gross entry

In the same function, `net_be` is computed and used for the mark eligibility check. But when `partial_gap or is_pending` is true and the mark is eligible, the subsequent recovery branch sets `target = entry_px` and calls `move_stop(..., target)`. It does not use `net_be`. This is a concrete inconsistency: the path can mark `be_moved=true` while the stop is only gross entry.

### 4. Live net-BE calculation is missing two cost inputs in the current caller

`LiveBroker.net_breakeven_price` reads `funding_accum` from the supplied position object and its own docstring explicitly says exit slippage is not applied. The `sync_partial_breakeven` caller constructs a synthetic position with entry, side, quantity and symbol, but does not pass `funding_accum`. Consequently funding defaults to zero in this calculation even though the function supports funding when supplied. It also cannot cover estimated stop-market slippage.

### 5. This code is not yet the whole exit policy

`SETTINGS.md` states the live workflow has `ZEUS_LIVE_EXIT_ENGINE=on`, ATR trail policy, and 30/70 TP fractions. The clock's early scale-out repair is another path. Any future change must audit all writers of stop/TP/quantity and prove that the exit engine, protection repair, and clock do not overwrite one another. Do not infer that disabling one helper disables all partial exits.

### Minimal safe correction scope for a future owner-approved implementation

- Define one canonical net-cost/stop-price calculator whose inputs are actual entry fills, current remaining quantity, accumulated funding, configured actual fee tier, and a conservative slippage allowance.
- Pass real accumulated funding into the calculator; fail closed (do not label gross entry as net BE) if required cost inputs are unavailable.
- In every BE placement path, use the same canonical target. In particular, remove the gross-entry target from the deferred partial-fill recovery path.
- Ensure only-tighten semantics and verify exchange order state after replacement; do not mark `be_moved` until the exchange stop is confirmed at the intended target.
- Treat 30% scale-out removal as a separate policy change. Audit `live_exit_engine`, initial TP order placement, repair helpers, and this scale-out function together.
- Add tests for long/short, nonzero funding paid/received, fee tiers, slippage, partial-fill recovery, exchange rejection, stale/missing funding, duplicate ticks, and restart recovery.

### Current decision

These source findings justify a focused exit-engine correction investigation. They do **not** justify editing production code yet: stop-market slippage cannot be guaranteed, the full exit-policy writers have not all been reconciled, and no fill-level replay has established that this exit design is profitable. Keep this PR research-only.
