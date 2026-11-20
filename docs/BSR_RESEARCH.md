# BingX Sweep Reversion (BSR) — research-only

Status: **prototype / no proven edge / no live integration**.

BSR tests whether a one-sided BingX USDT-M liquidity sweep is followed by
replenishment, fading price impact and a temporary deviation from an independent
fair-price reference. Binance spot is the initial reference; a symbol-specific
rolling basis must be estimated from synchronized observations before the
deviation is used.

## Safety boundary

- Isolated under astra_bot/research/bsr; not imported by Zeus or live VST.
- BingX client exposes public GET market-data methods only.
- No API keys, signatures, private endpoints, account access or order methods.
- A manual-only workflow captures public market data and uploads an audit artifact; it is not a trading workflow. No scheduled capture is enabled. REAL and current DEMO/VST settings are untouched.

## Event definition

LONG requires ordered pre-sweep, sweep-extreme and recovery snapshots; bid
notional depth must fall and replenish; mid must rebound; aggressive sell
notional must dominate (buyerMaker=true means seller aggressor); BingX must
remain discounted to external fair value; spread must be acceptable and the
recovered book bid-dominant. SHORT is symmetric.

Three snapshots are required: one REST depth response cannot identify a sweep
or distinguish cancellation from execution.

## Implementation

- models.py: immutable market-data and signal types.
- features.py: notional depth, spread, OBI, trade-flow imbalance, depth
  drawdown/recovery and deterministic candidate score.
- economics.py: maker/taker fees, slippage, signed funding (positive means
  long pays, negative means short pays) and risk-based sizing. `net_pnl` supports timestamped funding settlements so a position is charged only for settlements it crosses; the legacy single-rate field remains a fallback.
- bingx_public.py: read-only public REST bootstrap for depth, trades,
  premium index, funding history, V3 klines and Binance spot ticker.
- websocket_recorder.py: GZIP-aware public BingX Swap WS recorder for incremental
  depth, trades and mark price; persists raw normalized JSONL events and reconnects
  after an invalid depth sequence to obtain a fresh full snapshot.
- binance_spot_recorder.py: public Binance Spot trade-stream recorder using
  Binance's market-data-only WebSocket endpoint, with exchange and local receive
  timestamps for cross-venue basis measurement.
- basis.py: backward as-of join of BingX Perpetual prints to the latest earlier
  Binance Spot print; future spot events are never used and stale matches are excluded.
- scripts/bsr_capture.py: runs both venue recorders concurrently; writes separate
  BingX and Binance JSONL files.
- capture_audit.py and scripts/bsr_audit.py: read-only JSONL integrity audit; reports malformed byte offsets, duplicate trade IDs, exchange-to-receive latency percentiles, late/negative timestamps and depth sequence gaps. Original capture bytes are never changed.
- tests/test_bsr_research.py: deterministic feature, cost and depth-state tests.

Run both read-only recorders with: `python scripts/bsr_capture.py --output data/bsr/bingx_market.jsonl`. Add `--duration-seconds 3600` for a bounded one-hour capture; omit it to run until interrupted. The bounded mode cancels both recorder tasks and closes their WebSocket sessions at the deadline. Capture duration is a wall-clock limit, not a guarantee of complete market coverage.
BingX Swap events go to the requested file; Binance Spot trades go to the sibling
file named bingx_market_binance_spot.jsonl. Both use the same five symbol names.
It subscribes to {symbol}@incrDepth, {symbol}@trade and {symbol}@markPrice on
wss://open-api-swap.bingx.com/swap-market. Frames are GZIP-decoded and Ping is
answered with Pong. Local depth is unusable until an action=all snapshot arrives
on the active connection; action=update quantities replace absolute quantities,
zero deletes a level, and every disconnect invalidates the local book. Each
incremental update must advance lastUpdateId by exactly one. A missing, duplicate
or skipped ID invalidates and clears the local book; the recorder closes that
socket and reconnects so a fresh action=all snapshot is required before depth is
used again. No checksum is assumed.

REST is bootstrap only. Binance Spot trades are streamed independently.
`align_cross_venue_trades` performs a backward as-of join on exchange event time:
each BingX print is matched only to the latest Binance print at or before it,
with a configurable maximum age. It never uses future spot observations. Measure
local clock offset and feed latency before interpreting basis lead/lag. Spot trade
prints are not a spot order book, so they cannot substitute for spot depth.

Audit both files without modifying them:

`python scripts/bsr_audit.py --bingx data/bsr/bingx_market.jsonl --binance data/bsr/bingx_market_binance_spot.jsonl --clock data/bsr/clock_sync.jsonl --output data/bsr/capture_audit.json`

The report records malformed line byte offsets, duplicate `(venue, symbol, trade_id)`
keys, trade receive latency p50/p95/p99, depth message timestamp-to-receive age
p50/p95/p99, negative/late timestamps, parser and connection errors, and depth
snapshot/update sequence continuity. Depth message age is calculated from
`received_at_ms - payload.T` (or `payload.time` where that is the field present);
it is not the age of the underlying order-book state and must be interpreted with
server/client clock offset and BingX message-construction semantics. `ready_for_downstream_filtering` is a
conservative precondition, not a claim that the data is statistically valid or
that any trading edge exists. Inspect per-symbol metrics and exclude individual
invalid windows even when the overall precondition is true. Keep raw JSONL
immutable and store audit output separately.

The capture files are raw research evidence, not a ready-to-train dataset. Before
an event window is admitted, align exchange timestamps, quantify receive-time
latency and cross-venue clock skew, reject stale or sequence-invalid books, and
deduplicate trade IDs. Keep the original JSONL immutable; derived samples should
record source offsets, join age and exclusion reasons for reproducibility.

## API semantics

- Swap V2 depth: /openApi/swap/v2/quote/depth.
- Swap V2 public trades: /openApi/swap/v2/quote/trades.
- Swap V2 mark/index/funding snapshot: /openApi/swap/v2/quote/premiumIndex.
- Swap V2 funding history: /openApi/swap/v2/quote/fundingRate.
- Swap V3 klines: /openApi/swap/v3/quote/klines; rows are positional arrays.
- Binance spot reference: GET /api/v3/ticker/price via the market-data-only
  REST host `https://data-api.binance.vision`.

Funding interval must not be hardcoded to 8h or inferred from one snapshot.
Store actual funding timestamps and derive observed intervals from consecutive
settlements. Public REST limits are shared/throttled by IP; do not poll five
symbols at high frequency with REST.

## Economics

Initial fee defaults are assumptions, not verified account rates: Maker 2 bps
and Taker 5 bps per side. Replace them with the effective account/contract fee
schedule and actual fill fees. Slippage is separate. Funding sign is
position-dependent and must be joined by actual settlement timestamp. The event-aware PnL path sums `fundingRate` records in the open-position interval `(entry, exit]`; callers must pass one symbol's funding history only. It applies the summed rate to the supplied notional as a constant-notional approximation. For precise cash-flow reconstruction, use actual position quantity and mark price at each settlement.

Position sizing uses stop distance plus round-trip cost reserve and caps
notional at equity times leverage. It does not change Zeus risk or leverage.

## Research plan and acceptance gates

1. Capture synchronized book deltas, trades, mark/index, funding history and
   Binance spot for BTC, ETH, SOL, HYPE and INJ.
2. Measure sequence gaps, stale events, clock skew, duplicates, missing depth
   and reconnect recovery; exclude invalid windows.
3. Event study: forward returns at 5m/15m/30m/60m/120m after qualifying sweeps,
   before optimization.
4. Model queue-aware maker fill probability, partial fills, cancel latency,
   taker fallback, actual fees and funding settlements.
5. Chronological 60/20/20 split plus walk-forward; prevent symbol/time leakage.
6. Stress fees +50%, slippage x2/x3, maker fill rates 25/50/75%, delayed data,
   dropped WebSocket sequences, funding shocks and liquidation spikes.
7. Paper/VST only after edge survives net costs and operational tests.

Suggested targets, not performance claims: net PF >= 1.35, annualized Sharpe
>= 1.5, max drawdown <= 8%, positive out-of-sample expectancy, at least 300
independent completed trades, and no dependence on one symbol or a few
extreme sessions. Win rate is descriptive, not a gate.

## Known limitations

- Event detector is a three-snapshot prototype, not a production event stream.
- Fair price needs symbol-specific basis calibration and synchronization.
- OHLCV-only backtests cannot validate queue position or maker fill probability.
- No execution module is intentionally included.
- Thresholds are hypotheses; freeze them before out-of-sample tests.
