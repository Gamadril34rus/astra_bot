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


## Separate diagnosis: why recent live ticks placed no new entries (2026-11-11)

Inspected successful live-clock runs at 11:40, 11:45 and 11:50 UTC (run IDs 43013886468, 43014574492, 43015254805; job 130504586830 for the 11:50 run).

Observed in the 11:50 job:
- `ZEUS_ARM=1`: the entry hook was active.
- Session profile reported `normal arm=True`; this was not an `off` session.
- One existing position was present: BTC-USDT short. The feed correctly skipped a new BTC candidate because BTC was already open.
- HYPE long and INJ long candidate IDs were skipped as `signal_id already seen`.
- The channel feed wrote 0 candidates; Stage A reported `empty feed — 0 entries`.
- The final skip counters were all zero, including `htf_gate`, `filters`, `retest_not_triggered`, `cooldown`, `max_open`, `margin`, and `same_side_guard`.

Interpretation: in these inspected ticks, no new order was placed because the feed produced no fresh, unseen candidate. This is not evidence that a risk gate or PF HALT blocked entries. The current pipeline is idle upstream of the entry gates. Repeated old signal IDs are being deduplicated as designed.

Separate exchange/API reliability issue: the same run hit BingX `100410` cooldown/rate-limit responses on user income and position endpoints. Position reads succeeded earlier in the tick, but later protection/income calls encountered the cooldown. This can impair reconciliation/telemetry and must be rate-budgeted; it does not explain the empty candidate feed in that run.

### Immediate next diagnostic

Trace the candidate producer for channel / multi-top signals over the last 24 hours and compare:
- number of raw market structures detected per symbol;
- number surviving closed-candle confirmation;
- number converted to fresh signal IDs;
- number discarded as already-seen IDs;
- number reaching the arm feed.

Do not loosen confidence, HTF, retest, or risk gates until raw candidate counts prove that a specific filter is incorrectly suppressing valid signals. The 11:50 log shows those final gates were not the cause of zero entries.


## Follow-up: active signal paths and recent trade ledger (11 Nov 2026)

### Retest path is configured as shadow, not live

The current `.github/workflows/zeus-live-clock.yml` explicitly sets `ZEUS_RETEST_V1: "shadow"`. In `zeus_channel_boundary.py::_retest_mode`, shadow mode records a retest observation but does not set `would_signal=True`; `live_arm_feed.scan_symbol` therefore does not emit a pending row from that retest. This is a real, deterministic configuration restriction.

However, the four inspected live-clock runs (11:40, 11:45, 11:50, 11:55 UTC) did not log `channel_retest_shadow` / `shadow_retest` events. Therefore the shadow flag is a suppressed strategy path, but the available logs do not prove that a qualifying retest was present during those four ticks. Do not flip it to live on this evidence alone.

### The repeated IDs were previously entered, not downstream gate failures

The current `models/zeus_live_entries.jsonl` contains the exact IDs that the 11:40–11:55 feed skipped:
- `zeus_channel_boundary|INJ-USDT|long|1794384000000`
- `zeus_multi_top_breakdown|HYPE-USDT|long|1794380400000`

The trade ledger shows these IDs had already been entered and subsequently exited. The feed uses one signal ID per symbol/strategy/bar and suppresses re-entry on that same bar. This is expected deduplication, not evidence of PF/margin/HTF blocks. The latest four ticks therefore show no fresh eligible signal; they do not show a broken order-entry gate.

### Recent 24-hour ledger snapshot

From the committed `models/zeus_live_trades.jsonl`, using exit rows timestamped in the last 24 hours relative to the latest ledger event:
- 18 exits with a recognized strategy (8 channel, 10 multi-top);
- net proxy = `pnl - fees - funding_fee`: +567.79 USDT;
- 6 positive / 12 negative net-proxy exits;
- gross positive net-proxy exits +1,149.86 USDT; gross negative net-proxy exits 582.08 USDT; proxy PF 1.98;
- all 18 rows have `exit_price=null` and `exit_reason="income"`; one additional exit row has no strategy label and is excluded.

This is **not** a verified strategy-performance result. It is an income-ledger proxy; missing exit prices and unknown fill types prevent independent fill reconciliation, and the PnL/fee attribution can be incomplete or delayed. The positive aggregate is dominated by a few larger wins despite a 2:1 loss count. It is enough to reject the claim that the bot has made no trades or that every recent trade is losing; it is not enough to approve a strategy change or declare stable profitability.

### Pragmatic next step

Do not alter the deduplication or enable retest live. First add research-only funnel telemetry to a replay/capture path: per symbol, record the channel diagnostic `stage`, `reject_reason`, `has_channel`, `bars_outside`, retest mode/hit, and multi-top detector result, plus whether a candidate was suppressed by open-position or seen-ID checks. Then capture one full 4h cycle. This directly identifies which producer branch is quiet without relaxing trading rules.


## Follow-up: candidate confirmation semantics and ledger concentration (11 Nov 2026)

### Confirmed-close mismatch in multi-top / multi-bottom live feed

`live_arm_feed.scan_symbol()` fetches 4h candles and passes the complete returned series to the channel strategy, with an explicit comment that the last (forming) candle is included. If channel returns no signal, it passes the same 4h series to `detect_breakdown_from_ohlc()`; if that is silent, it fetches 1h and passes the complete 1h series to the same detector. The detector evaluates `closes[-1]` as the breakout candle and documents the condition as a close below/above the neckline. It has no closed-candle exclusion or close-time validation.

Therefore the live multi-top/bottom path can treat an **in-progress candle's current price** as a confirmed close-break. The candidate ID is still stamped with that candle's open time. This is a concrete semantic mismatch between the detector's stated rule ("close break") and the live data passed to it. It can create transient intrabar breakouts and early entries that would disappear by candle close. The same forming-bar exposure exists in channel strategy by deliberate paper parity; it needs separate evaluation, not an automatic global change.

Two recent HYPE multi-top long entries were stamped with 1h/4h bar IDs at 10:00 and 11:00 UTC on 11 Nov and were exited about 10 and 19 minutes after entry. This timing is consistent with an intrabar candidate, but without the historical candle snapshots it does **not** prove that either signal reversed before close. Treat as a high-priority testable defect hypothesis, not a proven cause of those losses.

### Correct 24h ledger interpretation

The ledger contains 19 exit rows in the 24h window. Eighteen carry a recognized strategy (10 multi-top, 8 channel); one additional ETH exit is missing its strategy label and has `exit_reason=no_fills`, a negative fee value, and no matching entry in the 24h entry set. Excluding that unattributed row:
- recognized-strategy net proxy: +567.79 USDT;
- 6 positive / 12 negative exits;
- channel: +313.02 USDT across 8 exits (2 positive / 6 negative);
- multi-top: +254.76 USDT across 10 exits (4 positive / 6 negative).

The positive net is highly concentrated: channel's +571.04 HYPE short and multi-top's +154.00 SOL short / +155.96 BTC short dominate their respective totals; many smaller exits are net negative. This is still an income-ledger proxy: all 18 recognized exit rows have no exit price, so it cannot establish actual exit fills or exact holding periods. Some entry-to-exit fee/notional ratios are near 0.10%, which warrants a separate fee-attribution audit before using these numbers for parameter selection.

### Next research action

Build a replay test that feeds the detector a candle sequence where the current forming candle pierces the neckline but later closes back above/below it. Assert that no live-eligible signal is produced until that candle is finalized. Then compare:
1. current forming-candle behavior;
2. closed-candle-only confirmation with execution on the next available price;
3. closed-candle confirmation plus a retest entry.

Run on archived 1h/4h OHLCV with costs and report per-symbol, per-strategy results. Do not change live settings or merge a fix until this replay quantifies the trade-off.


### Isolated corrective draft

A separate draft PR was opened: [#355](https://github.com/Gamadril34rus/astra_bot/pull/355). It applies closed-candle confirmation only to the multi-top/multi-bottom fallback, retains the current forming candle solely as the execution-price reference, and stamps the signal with the confirmed bar timestamp. It now also brings the channel strategy into parity with the existing A5 rule: evaluate closed candles while passing the current forming-candle close separately as `current_price`. The same behavior is applied to the multi-top 4h/1h fallback. It adds tests for channel inputs, both fallback timeframe paths, and an intrabar-only false break.

The INJ short opened in the 12:00 UTC VST tick used a signal ID stamped with the 12:00 4h candle, and the feed evaluated that forming candle at 12:03 UTC. Under the proposed closed-bar rule, that entry would not have been eligible until the 4h candle closed. This does not prove the trade was wrong; it shows why the rule matters and why the draft must be replay-tested before merging.

PR #355 is draft-only and unmerged. CI is pending. No production or master code/config change has been made.


### New live-clock tick after the quiet window (12:00 UTC)

The next master live-clock run, `43016677831` (job `130509402713`), no longer had an empty feed. It generated one `zeus_channel_boundary` candidate for INJ-USDT short and opened it in VST at approximately 12:03:09 UTC:
- signal ID: `zeus_channel_boundary|INJ-USDT|short|1794398400000` (12:00 UTC 4h bar);
- recorded fill: 7.896; quantity 783.88617651; leverage 6;
- initial stop: 8.40397623; TP1: 7.62281307;
- both stop and TP protection placement events were written; `pending_verify=false` in the committed position snapshot.

This confirms the 11:40–11:55 empty-feed finding was a time-bounded quiet interval, not a persistent dead scanner. The new entry came from the channel strategy, not the multi-top fallback, so it neither validates nor invalidates the multi-top closed-candle correction. Monitor this VST trade through its exit; do not infer profitability from an open position.


## Follow-up: session BE threshold is not reaching live BE sync (11 Nov 2026)

### Runtime proof

The 12:12:02 UTC live journal has:
- BTC short: `mfe_r=-0.2181`, `trigger_r=0.50`;
- INJ short: `mfe_r=0.0079`, `trigger_r=0.50`.

At 12:12:33 UTC the same tick records `session_profile=normal`, `be_trigger_r=0.40`, `tp1_rr=0.55`. Thus the effective session profile and the trigger actually consumed by `sync_partial_breakeven` disagree in the same cycle.

### Source cause

In `scripts/zeus_live_clock.py`, `sync_partial_breakeven()` is called before `apply_session_to_environ()`. The BE function reads `ZEUS_BE_TRIGGER_R` from the process environment (workflow default 0.5) when no explicit argument is supplied. Each GitHub Actions tick is a fresh process, so the session helper's environment mutation at the end of one tick does not survive into the next tick. Consequently, the BE sync keeps using 0.5 even when normal/thin/off profiles specify 0.4/0.3.

This is a real configuration-wiring defect, not a parameter-performance conclusion. It has not yet moved the current INJ stop because its observed MFE was only 0.0079R, below both 0.4R and 0.5R. BTC was negative MFE at the sample. Therefore no immediate stop adjustment was warranted based on these snapshots.

### Corrective proposal

Draft PR [#356](https://github.com/Gamadril34rus/astra_bot/pull/356) moves session-profile application before BE sync and passes the selected `be_trigger_r` explicitly. It adds a regression test: with a 0.40R profile, BE must trigger at 0.45R. Draft only; do not merge until its full quality gates pass and owner reviews the behavior change.

### Current live-state check at 12:12 UTC

Two VST positions remained open: BTC-USDT short and INJ-USDT short. The clock reconciled 4 protective orders with `missing_stops=0`; feed skipped both already-open symbols, produced zero candidates, and reported no downstream gate skips. This is an expected no-entry tick, not evidence of a dead scanner.
