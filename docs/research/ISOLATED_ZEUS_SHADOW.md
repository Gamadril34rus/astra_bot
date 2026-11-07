# Isolated ZEUS shadow — protocol v1 (not enabled)

## Owner-authorized scope

- **A — baseline:** live arm-feed generator: channel_boundary, then multi_top_breakdown only if channel emits no signal (4h then 1h fallback).
- **B — Grok candidate:** BTC/ETH/SOL/HYPE only; channel_boundary SHORT only; multi_top_breakdown detector unchanged, including the symmetric multi-bottom fallback.
- **C — baseline plus opposing-zone veto:** same baseline signals as A; block only OUTSIDE_NEAR (distance <= 1.00%).

Paper channel+wedge is not A and is not used as a substitute.

## Isolation boundary

The pure accounting/classification module astra_bot/research/isolated_shadow.py imports no broker, exchange adapter, live wiring, order API, working queue, or project state path. scripts/zeus_shadow_capture.py is an explicit one-shot CLI. It imports only the Candle model, pure channel/multi-top detectors and pure volume-zone builder. It uses aiohttp direct GET calls to public BingX swap quote endpoints:
- /openApi/swap/v3/quote/klines
- No private endpoint or authenticated client is instantiated.
- No key/secret is accepted; the CLI refuses to run if BINGX_API_KEY or BINGX_API_SECRET exists in its environment.
- No loop, scheduler, workflow, cron, broker, POST, DELETE, or working-queue access exists.
- Output path is mandatory and rejected if inside this checkout. Use an external path.

This is a software-level isolation contract, not an OS sandbox. Before a deployment, use a dedicated unprivileged OS user/container, read-only repository mount, write access only to the external data directory, outbound allowlist limited to public quote endpoints, and network policy denying authenticated/private exchange endpoints. No workflow or cron is connected in this PR.

## Signal source and causality

A and C are defined to receive the same source events. B filters that common raw event stream by universe and channel direction. The one-shot capture currently pulls 4h and 1h candles for the five-symbol A universe, two GETs per symbol (10 calls total). The live strategy uses the forming 4h candle close as its price input; the shadow preserves that input for channel evaluation. The zone builder excludes the final forming candle and builds zones from preceding 4h candles. The source closed-bar timestamp is the open timestamp of candle[-2].

Receive timestamps are recorded immediately after each response. The 30-second age check is implemented in the zone classifier. However, the capture is not a continuously synchronized market-data feed: the two timeframe responses can arrive at different times, and HTTP Date header parsing / server-clock skew is not yet a strict consistency gate. Any snapshot not provably fresh at decision time must be UNKNOWN; future deployment needs a stricter synchronized quote/snapshot adapter before using C.

## Zone semantics

For LONG, choose the lower boundary of the nearest zone wholly above price. For SHORT, choose the upper boundary of the nearest zone wholly below price. Distance = 100 * abs(boundary - predecision_price) / predecision_price. Exactly 1.00% is OUTSIDE_NEAR. IN_ZONE, EMPTY_ZONES, NO_OPPOSING_ZONE and UNKNOWN remain distinct and are not blocked by C. No centre distance or accumulated minimum distance is used.

## Portfolio and execution status

The accounting primitive has independent per-instance capital/positions, MAX_OPEN, unique attempt/order/position/fill IDs, a provisional 1% risk fraction, 30% gross-exposure cap, 6x notional ceiling, 0.05% taker fee per side and 5 bps slippage per side. It supports event-idempotent partial/final closes and per-position PnL aggregation.

**This PR does not yet contain a complete three-portfolio event replay.** The CLI captures one snapshot and emits candidate signals; it does not yet persist and feed identical complete event streams into A/B/C portfolio state machines. Nor does it model next-available execution quotes, stop/TP collision resolution, gaps, partial-fill rules, funding accrual, candle-by-candle stop management, daily/weekly loss gates, session arm windows, cooldown, side-cluster caps, INJ slot reserve, heat governor, leverage ladder, instrument filters, or the full live exit-engine. The current portfolio class is a tested accounting primitive, not a performance simulator. PnL/PF/R outputs must not be represented as experimental results.

The live canon in SETTINGS.md includes MAX_OPEN=5, same-side cap 3 / hard cluster cap 4, strategy-symbol-side cooldown 20m, INJ reserve 2, FRESH_MAX_SEC 2700, confidence risk ladder, leverage settings and live-only HALT/risk gates. The generic project risk values in config/settings.yaml are not substitutes for Zeus live values. Exact faithful replication requires an owner-approved frozen protocol and additional implementation. Do not claim full live rule parity.

## Fees, slippage, funding

The BingX adapter source documents taker 0.05%, maker 0.02%, with paper using taker fees. Current provisional engine assumes taker on both sides. 5 bps slippage each side is an explicit uncalibrated research assumption. Funding is not implemented and remains UNKNOWN. Historical funding rates and settlement timestamps must be fetched from public endpoints before any valid net-PnL comparison.

Execution must use a price observable after the signal and model latency. Signal reference/entry price is not an execution fill. This capture does not yet implement delayed fill replay. Ambiguous stop/TP hits and gaps are not resolved; they must be handled pessimistically or marked UNKNOWN, never selected for favorable PnL.

## Accounting semantics

signal_id identifies a source signal, not a trade. Each attempted entry receives an attempt_id; virtual order, fill and position IDs are distinct. Partial exits are separate fills linked to one position. Idempotency uses event IDs. Closed-position net PnL is aggregated from fills, fees and funding once; it must not be added to a second aggregate exit PnL. Original cash risk is retained per position. R remains UNKNOWN until implemented against that original risk. PF must be calculated from closed-position net results with a persisted contributing-position list.

## Request budget and future protocol

Current one-shot capture budget is 10 public GET calls per invocation (2 per symbol), no retries and no daemon. This is not a global exchange quota guarantee: repeated manual invocations could exceed an account/IP-wide limit. Before any future run, separately establish BingX limits and other bot/archive traffic, add a shared global token bucket and egress restriction, and obtain owner approval. No live data has been captured by PR preparation.

The intended experiment is 30 calendar days, at least 50 closed positions in every variant; otherwise report insufficient data and do not extend automatically. Historical replay is not independent OOS. Freeze code, config, costs, timing, and execution rules before the start. No mid-run tuning.

## Tests

Synthetic tests cover boundary direction, exactly 1%, >1%, separate zone states, stale/future snapshots, B filters, per-portfolio MAX_OPEN/independence, repeated signal IDs, and idempotent partial/final closes. Static checks assert no POST/DELETE, no live wiring and no working state paths. These tests validate primitives only; they do not establish full runtime isolation or execution fidelity.
