# Isolated ZEUS shadow — replay protocol v3 (not enabled)

## Scope and frozen source stream

This is a research-only implementation on a dedicated branch. It is not merged, not scheduled, and has not collected public market data.

- **A:** live live_arm_feed.scan_symbol channel strategy and fallback precedence.
- **B:** BTC/ETH/SOL/HYPE only; accept channel SHORT, reject channel LONG, accept multi-top/bottom fallback either side.
- **C:** same baseline stream as A; reject only when the nearest opposing zone **boundary** is within 1.00%, inclusive.

live_arm_feed.scan_symbol and paper_channel_config() were inspected at the PR base. Despite the historical function name, paper_channel_config() is the active channel configuration passed by _channel_strategy() to the live arm feed. It is not scripts/zeus_research_wrap.py:channel_capital_config() and not the separate wedge experiment. The capture freezes these settings: enabled, lookback 48, width 0.01–0.12, parallel clone / tolerance 0.25, min RR 2.0, breakout hold and retest, hold 2–5 bars, min stop 1%, HTF bias, TP parameters 1.5/2.0 and fractions 0.4/0.6. The channel evaluator receives the current 4h candle series including its forming last bar, as the live feed does.

## One-event fan-out — non-negotiable

One baseline generator selects at most one event per symbol:
1. channel signal if present;
2. otherwise 4h multi-top/bottom;
3. otherwise 1h multi-top/bottom;
4. otherwise no event.

A, B and C receive that exact same event. A variant can accept/reject it, but cannot change it. If channel LONG exists while a multi-top signal could also exist, the baseline is channel LONG and B records B_CHANNEL_SHORT_ONLY; B cannot run its own detector or substitute multi-top. A synthetic test covers this collision. The capture does not run fallback detectors after a channel signal, matching the actual live control flow.

## Zone veto

- LONG: lower boundary of the nearest zone wholly above predecision price.
- SHORT: upper boundary of the nearest zone wholly below predecision price.
- distance_pct = 100 * abs(boundary - price) / price.
- <= 1.00% is OUTSIDE_NEAR; C blocks only that state.
- IN_ZONE, EMPTY_ZONES, NO_OPPOSING_ZONE, OUTSIDE_FAR, UNKNOWN remain distinct.
- Missing, future-dated or older-than-30-second snapshots are UNKNOWN. Zone centre and shadow min-distance are not used.

## Entry execution model

The replay never fills at the signal reference price. It chooses the first quote whose exchange timestamp is strictly later than signal decision time. It refuses the first quote if timestamp/receive-time consistency is invalid or delivery lag exceeds 30 seconds; it does not skip a bad first quote to cherry-pick a later one. The current maximum wait for an entry quote is 45 minutes; no eligible quote yields UNKNOWN and no fill.

For a market-style entry it uses the executable side of the quote: ask for LONG, bid for SHORT. It requires displayed size on that side. Requested size is capped by the quote's displayed top-of-book quantity; a smaller amount becomes an explicit partial entry. Missing price or size is UNKNOWN, never zero. The quote timestamp, receive timestamp, quote ID, price, quantity and fee are retained in the event/fill ledger.

This is an observable-quote replay model, not a guarantee that the full displayed quantity would be filled by BingX at that exact price. It uses only best bid/ask and displayed top-level quantity; it does not replay full order-book depth, market impact, order latency after observation, matching priority or exchange-specific market-order slippage.

## Stop/TP and exit execution model

A fully closed OHLC bar may create an exit intent; an OHLC touch itself is **not** an execution fill. The exit is filled only from the first later executable-side quote with available displayed quantity.

- Stop gap: if a bar opens beyond the stop, the bar open is recorded as adverse trigger reference. The actual exit price is still the later executable quote, not the bar open.
- Intrabar stop touch: an intent is created at stop reference; next-quote execution determines the fill.
- Stop and TP both touched in the same OHLC bar: stop-first conservative resolution. The bar cannot reveal touch order, so this is a registered adverse assumption, not discovered market truth.
- TP gap: a gap through a resting TP limit establishes the TP as the first trigger. The later quote must still be marketable against the limit (LONG exit bid >= TP; SHORT exit ask <= TP); otherwise no fill is recorded and the pending TP waits for a later quote.
- Partial exits use independent fill IDs linked to one position. Filled quantity is accumulated per TP level; retries are idempotent by event ID. Multiple partial fills aggregate into one closed position for trade-level R/PF.

The default virtual arm TP1 follows the live session profile and represents 30% of initial quantity. The remaining 70% runner is not modeled with the full live exit plan yet. Synthetic fixtures can provide explicit TP levels to test schedule mechanics. A bar spanning the entry timestamp is skipped completely to avoid using pre-entry extremes.

The replay currently models the static initial stop, session-profile TP1, and 48-hour max-hold trigger. It does **not** yet reproduce the full live exit engine: D1 structural tightening, BE, ATR trailing, MAE_CUT, regime exit, VOL_EXPANSION, ratchet/pct stop-pull policy, stop modifications and actual exchange trigger-order lifecycle. Those omissions can materially change exits and PnL.

## Costs, funding and unknowns

- Configured fee: 0.05% taker on entry and every exit fill. This matches the documented default BingX perpetual taker rate in the repository's adapter/cost model, but is still a configured model; maker fills, fee tiers, discounts, fee asset conversion and exact realized exchange commission are not sourced from a fill tape here.
- No artificial 5 bps slippage is added on top of bid/ask. Quote spread is reflected by using bid or ask. Market impact beyond displayed best-level size is unknown.
- Funding is supplied only as explicit signed cash events or a source-verified cumulative funding reconciliation covering the whole position lifecycle. Missing rate/amount/coverage leaves the position incomplete; incomplete closed positions are excluded from completed-position PF/R, make PF completeness false and mark-to-market UNKNOWN. There is no auto fetch of funding-rate history in this PR.
- Funding reconciliation is not proof by itself: before a run, source records, settlement windows and BingX position identifiers must be reconciled and reviewed.
- Net R per position = net realized PnL / original cash risk at entry. Partial exits do not change the initial denominator.
- Closed-position PF uses complete lifecycle net PnL once per position, not partial fills as separate trades. Mark-to-market PF includes complete closed positions plus open positions at explicit marks and estimated taker exit fee; it is UNKNOWN if any open mark or funding coverage is missing.
- Marked drawdown uses explicit synchronized mark events and peak marked equity. If marks/funding are not current, drawdown status is UNKNOWN rather than carried forward as a current result.

## Virtual live-entry gates and their parity limits

Each A/B/C portfolio independently applies the following counterfactual gates before new entries:

| Gate | Virtual rule | Live source / parity |
|---|---|---|
| MAX_OPEN | 5 open positions | Matches live_arm.py |
| Session | MSK weekday/weekend fat/normal/thin/off schedule; off blocks new entries | Hours and BE/TP1 presets copied from live_session.py; env force/lock overrides not modeled |
| Cooldown | 20 minutes per strategy/symbol/side | Matches live_arm.py constant |
| Same-side cap | Block when 4 positions already have same side | Matches hard cluster max 4; not a model of every exchange-side UPNL detail |
| Same symbol | One open position per symbol | Matches live arm conflict guard intent |
| INJ reserve | Block INJ when pre-entry open count > 3 at MAX_OPEN=5 / reserve=2 | Matches inj_slot_blocks |
| Risk ladder | confidence >= .88: 3%; >= .80: 2%; >= .70: 1.5%; otherwise 1% | Matches conf_risk_pct / live_arm.py defaults |
| Heat | After 3 complete consecutive losing positions, halve next risk tier until UTC day roll or a complete win | Threshold/day behavior from zeus_heat.py; live caller applies tier ×0.5 |
| Daily / weekly loss | 4% / 6% of virtual cash equity using modeled cash flows | Thresholds match live_risk.py; treatment of null/missing PnL, resets and live-wallet equity differ |
| Virtual HALT | n >= 5 complete closed positions, PF < 1 over last 20 complete positions | Threshold/window match; live uses kill epoch/window and can count partial_fill rows separately and skip scratched exits. Virtual position-level accounting is intentionally different |
| Leverage / notional | 6x notional cap; 50,000 USDT per-position value default | 6x is the documented workflow forced leverage; 50K is live_arm_wiring.max_position_value_usdt() default. Runtime env override is unknown and is not imported |
| Instrument quantity filters | Not yet modeled | Live resolves step size and quantizes quantity; minimum quantity/notional, precision and exchange-specific rules remain a blocker |

The virtual cash model is not the live broker's wallet/margin ledger. It does not reproduce maintenance margin, liquidation, margin mode, funding settlement collateral, cross-position correlation exposure, or every error/reconciliation gate. Daily/weekly accounting does not emulate live null-PnL imputation and manual reset/epoch files. These must not be described as exact live parity.

## Offline replay and filesystem/network boundary

scripts/zeus_shadow_replay.py replays an externally supplied JSONL event tape. Event types are signal, closed bar, funding, funding coverage and mark. It writes checkpoint state and output only to caller-specified paths outside the repository; event IDs and portfolio states survive restarts. It has no HTTP client, exchange client or public-data capture path.

deploy/shadow/compose.offline.yml defines an offline container profile:
- network_mode: none — no external network interface/default route;
- repository mounted read-only at /workspace;
- root filesystem read-only; only caller-supplied external data directory mounted writable at /data;
- UID/GID 65532, all capabilities dropped, no-new-privileges, PID/memory/CPU limits and small noexec/nosuid/nodev /tmp;
- no secret, Docker socket, host PID/network or trading-key mounts.

scripts/verify_shadow_runtime.py checks the effective running process, mounts, capabilities, NoNewPrivs, non-loopback interfaces/default route, and proves repo writes fail while /data writes succeed. **This runtime verifier has not been executed in a real Docker container in the current development environment** (Docker CLI/runtime is unavailable here). Therefore evidence so far is code/configuration-level, not a runtime attestation. It cannot prove a hostile/compromised host or Docker daemon, nor prevent an administrator from overriding the profile. The actual host must run the verifier and retain its output before an experiment.

The separate zeus_shadow_capture.py remains a manual one-shot public candle capture script, not part of the offline profile. It makes 10 public candle GETs per invocation and has not been run. It does not capture execution quotes or funding events and cannot by itself supply a valid replay tape. The global BingX request budget shared with the working bot and archive is not proven; no capture network profile, scheduler, workflow or cron is connected.

## Remaining blockers before any experiment

1. A reviewed public quote-tape source with timestamp/receive-time synchronisation, displayed sizes and a proven request budget shared with all BingX traffic.
2. Verified funding-rate/settlement history and reconciled per-position funding coverage.
3. Full live exit-engine replay, including stop adjustments, runner handling, forced exits and exchange protective-order behavior, or an explicit owner-approved simplification protocol.
4. Instrument filters, margin/liquidation, actual live risk equity and exact reset/epoch/partial-PF semantics.
5. Successful current CI, plus runtime execution of the isolation verifier on the intended host and retained attestation.
6. Separate explicit owner permission to begin the 30-calendar-day experiment. Minimum 50 fully closed positions per variant; otherwise report insufficient sample and do not extend automatically.

No public candle collection, replay experiment, live order, or merge has been performed by this PR work. The intended 30-day / 50 closed-position rule is only a future protocol, not a start date or guarantee.

## Tests

Synthetic tests cover one-event A/B/C fan-out and B's no-fallback rule; boundary direction and 1% threshold; zone freshness/UNKNOWN; quote selection after signal; bid/ask side and missing size; session/cooldown/MAX_OPEN/side/INJ/heat/day-week/virtual-HALT gates; stop gaps, simultaneous stop/TP touch, TP limit marketability and partial fills; missing/reconciled funding; fees, initial-risk R, closed/open PF and marked drawdown; restart/idempotency; no fabricated fills; and static sandbox configuration. They prove these synthetic software rules only — not exchange fills, global request quota or actual host isolation.
