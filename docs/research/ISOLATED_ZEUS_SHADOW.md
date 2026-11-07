# Isolated ZEUS shadow — protocol v2 (not enabled)

## Owner-authorized scope

- **A — baseline:** the actual `live_arm_feed.scan_symbol` path: evaluate the 4h `paper_channel_config()` channel strategy first; only if it returns no signal, evaluate multi-top/bottom on 4h; only if that is also silent, fetch/evaluate 1h multi-top/bottom.
- **B — Grok candidate:** receives the exact same single selected baseline event as A and C. BTC/ETH/SOL/HYPE only; reject a channel LONG; accept channel SHORT and either direction from the multi-top/bottom fallback. B does not re-run detectors or promote another candidate after rejecting a channel LONG.
- **C — baseline plus opposing-zone veto:** receives the same baseline event as A; blocks only OUTSIDE_NEAR (distance <= 1.00%).

The capture freezes the actual `live_arm_feed.paper_channel_config()` values from the base revision. This function name is historical; it is the active channel configuration used by `scan_symbol`. It is not `scripts/zeus_research_wrap.py:channel_capital_config()`, and it is not the separate paper wedge/channel experiment. The capture preserves the live feed's forming 4h candle close as the channel strategy's price input.

## One-event fan-out (critical invariant)

For each symbol snapshot, exactly one baseline generator selects at most one event:

1. channel signal, if present;
2. otherwise 4h multi-top/bottom signal, if present;
3. otherwise 1h multi-top/bottom signal, if present;
4. otherwise no event.

That selected event is copied unchanged to A, B and C. Variant filtering occurs only after selection. In particular, if the selected event is channel LONG and a multi-top/bottom candidate could also have been detected, B records `B_CHANNEL_SHORT_ONLY` and skips the event. It must not take the competing multi-top candidate. A synthetic test asserts this exact collision case.

The capture output retains detector fallback candidates as diagnostic fields only when fallback evaluation was reached. A channel event prevents multi-top detector evaluation, matching the actual live control flow.

## Isolation boundary

The pure accounting/classification module `astra_bot/research/isolated_shadow.py` imports no broker, exchange adapter, live wiring, order API, working queue, or project state path. `scripts/zeus_shadow_capture.py` is an explicit one-shot CLI. It imports only the Candle model, pure channel/multi-top detectors and pure volume-zone builder. It uses aiohttp direct GET calls to public BingX swap quote endpoints:
- `/openApi/swap/v3/quote/klines`
- No private endpoint or authenticated client is instantiated.
- No key/secret is accepted; the CLI refuses to run if `BINGX_API_KEY` or `BINGX_API_SECRET` exists in its environment.
- No loop, scheduler, workflow, cron, broker, POST, DELETE, or working-queue access exists.
- Output path is mandatory and rejected if inside this checkout. Use an external path.

This is a software-level isolation contract, not an OS sandbox. Before a deployment, use a dedicated unprivileged OS user/container, read-only repository mount, write access only to the external data directory, outbound allowlist limited to public quote endpoints, and network policy denying authenticated/private exchange endpoints. No workflow or cron is connected in this PR.

## Signal source and causality

The live channel path evaluates the last 4h candle (which may be forming). The multi fallback also evaluates the 4h series first and fetches 1h only when 4h has no candidate. The capture records receive timestamps immediately after each HTTP response and records the 4h forming-candle close used as the predecision price. The zone builder treats the final candle as forming and excludes it; source closed-bar timestamp is the open timestamp of candle[-2].

The capture fetches both timeframes in its one-shot snapshot (two public GETs per symbol), so its transport schedule is not identical to the live feed's conditional 1h fetch. The detector precedence and candle inputs are preserved, but receive-time simultaneity is not. HTTP Date header parsing / server-clock skew is not yet a strict consistency gate. Any snapshot not provably fresh at decision time must be UNKNOWN; a synchronized public market-data adapter is still required before a valid run.

## Zone semantics

For LONG, choose the lower boundary of the nearest zone wholly above price. For SHORT, choose the upper boundary of the nearest zone wholly below price. Distance = 100 * abs(boundary - predecision_price) / predecision_price. Exactly 1.00% is OUTSIDE_NEAR. IN_ZONE, EMPTY_ZONES, NO_OPPOSING_ZONE and UNKNOWN remain distinct and are not blocked by C. No centre distance or accumulated minimum distance is used.

## Event replay and portfolio status

`ShadowReplay` now fans one immutable signal event into three independent `VirtualPortfolio` instances. Each portfolio owns its capital, positions, attempts, fills, closed positions, exposure limits and MAX_OPEN. A B rejection is recorded as a rejected attempt; it does not change the signal event or create a replacement signal. The replay accepts explicit entry execution prices and explicit exit/funding events only; missing entry execution price records `UNKNOWN_EXECUTION_PRICE` and creates no fill. State export/import preserves open/closed positions, fills, attempts and seen event IDs for restart/idempotency tests.

The accounting primitive uses provisional defaults: initial capital 100,000; risk fraction 1%; MAX_OPEN 5; gross exposure cap 30%; max notional 6x; taker fee 0.05% per side; slippage 5 bps per side. These are simulation defaults, not a verified full live configuration. 5 bps slippage is uncalibrated. R remains UNKNOWN. PF is computed from closed-position net PnL with a per-position contributor list; partial exits aggregate into one closed position and are not counted as separate trades.

**This is not yet a complete market-candle replay or execution simulator.** The capture does not automatically turn subsequent candles into next-available fills/exits, and the event replay does not infer execution from OHLC. It does not model stop/TP collision resolution, gaps, partial-fill rules, candle-by-candle stop management, funding-rate schedule ingestion, daily/weekly loss gates, session arm windows, cooldown, side-cluster caps, INJ slot reserve, heat governor, confidence/leverage ladder, instrument filters, or the full live exit-engine. It therefore cannot produce valid strategy performance/PF/R conclusions from captured candles. It is an explicit event-accounting harness awaiting a separately specified and validated execution-event generator.

The live canon in SETTINGS.md includes additional live limits and gates. Exact parity for all of those remains unimplemented and must not be inferred from the current provisional portfolio defaults. The generic project risk values in config/settings.yaml are not substitutes for Zeus live values.

## Fees, slippage, funding

The provisional engine assumes taker fees on entry and exit. 5 bps slippage each side is an explicit uncalibrated research assumption. Funding can be supplied as explicit cash events on exit but is not accrued from exchange funding-rate history. Historical funding rates and settlement timestamps must be ingested before any valid net-PnL comparison. Ambiguous stop/TP hits and gaps must be handled pessimistically or marked UNKNOWN, never selected for favorable PnL.

## Request budget and future protocol

Current one-shot capture budget is 10 public GET calls per invocation (2 per symbol), no retries and no daemon. This is not a global exchange quota guarantee: repeated manual invocations could exceed an account/IP-wide limit. Before any future run, separately establish BingX limits and other bot/archive traffic, add a shared global token bucket and egress restriction, and obtain owner approval. No public market data has been captured by PR preparation.

The intended experiment is 30 calendar days, at least 50 closed positions in every variant; otherwise report insufficient data and do not extend automatically. Historical replay is not independent OOS. Freeze code, config, costs, timing, and execution rules before the start. No mid-run tuning.

## Tests

Synthetic tests cover LONG/SHORT boundary direction, exactly 1%, >1%, distinct zone states, stale/future snapshots, B universe/direction rules, the simultaneous channel-LONG plus multi-top collision, shared A/B/C event identity, 4h-before-1h fallback, independent portfolio capital/MAX_OPEN, repeated signal IDs, partial-close idempotency, PF contributors, state export/import and refusal to fabricate fills without an execution price. Static checks assert no POST/DELETE, no live wiring and no working state paths. These tests validate software primitives only; they do not establish full runtime isolation, exchange request-budget safety or execution fidelity.
