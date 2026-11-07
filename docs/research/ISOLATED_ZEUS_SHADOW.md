# Isolated ZEUS shadow — replay protocol v3 (not enabled)

## Scope and frozen source stream

This is a research-only implementation on a dedicated branch. It is not merged, not scheduled, and has not collected public market data.

- **A:** live `live_arm_feed.scan_symbol` channel strategy and fallback precedence.
- **B:** BTC/ETH/SOL/HYPE only; accept channel SHORT, reject channel LONG, accept multi-top/bottom fallback either side.
- **C:** same baseline stream as A; reject only when the nearest opposing zone **boundary** is within 1.00%, inclusive.

The source was checked against current master: live_arm_feed.py blob b57d27cf7e5581d6305fcaeb66719c04f14680a4. scan_symbol() calls _channel_strategy() / paper_channel_config(), evaluates the 4h series including its forming last bar, and only if channel is silent tries 4h multi-top/bottom, then fetches 1h and tries that fallback. The capture now follows that conditional fetch order (one 4h GET per symbol; one additional 1h GET only if both channel and 4h multi are silent). This is the observed live arm-feed signal precedence, not proof that the current bot session has ZEUS_ARM enabled or that a pending signal is executable. It is not scripts/zeus_research_wrap.py:channel_capital_config() and not the separate wedge experiment. The capture freezes these settings: enabled, lookback 48, width 0.01–0.12, parallel clone / tolerance 0.25, min RR 2.0, breakout hold and retest, hold 2–5 bars, min stop 1%, HTF bias, TP parameters 1.5/2.0 and fractions 0.4/0.6. The channel evaluator receives the current 4h candle series including its forming last bar, as the live feed does.

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

## Variant A is not a live replica

A is the closest *signal-source comparator* in this research harness, not a byte-for-byte or accounting-equivalent copy of the live bot. The current evidence is insufficient to claim full live parity.

| Area | Virtual A in this PR | Live behavior / unresolved difference |
|---|---|---|
| Signal source | Uses the observed live arm-feed channel settings and channel → 4h multi → 1h multi precedence. | This copies the inspected source path, not the effective runtime decision state. ZEUS_ARM, session-profile overrides, persisted plans and exact environment settings have not been attested. |
| PF and trade count | PF is calculated per fully closed virtual position. A position with partial exits is aggregated and contributes once after its lifecycle is complete. | `astra_bot/decision/live_risk.py::_read_exit_pnls()` includes rows with `event=partial_fill` by default (`ZEUS_PF_INCLUDE_PARTIAL` defaults to `1`) alongside exit rows. Live therefore may count realized scale-out legs separately, while virtual A counts one completed position. The sample unit and PF population differ. A's PF and trade count cannot be compared directly with live PF/count without fill-to-position reconciliation and one matched metric definition. |
| Capital and equity | Each virtual portfolio has its own synthetic cash ledger, initialized to 100,000 USDT by default. Fees, modeled funding and explicitly supplied marks update that virtual ledger. | Live `LiveBroker.risk_budget()` sizes from `client.get_balance_usdt()`; `LiveBroker.equity()` separately returns `min(wallet, hard_cap)`. The virtual fixed starting capital is neither live wallet balance nor live equity. Maintenance margin, cross/isolated margin effects, liquidation, wallet transfers and actual account-level funding settlement are not reproduced. Virtual returns/drawdown are not live account returns/drawdown. |
| Exit engine | The replay supports a static initial stop, session-profile TP1, explicit test TP schedules, and a 48-hour max-hold trigger. TP1 defaults to 30%; the 70% runner is not modeled as the full live runner. | Current live code includes net break-even, ATR trailing, MAE_CUT, regime exits, scale-out handling and exchange protective-stop/order reconciliation (for example `exit_controller.py` and `live_broker.py`). The research replay does not yet reproduce these live paths end-to-end, nor the full runner, structural/D1 stop adjustments, all stop-pull policies or venue-side protective-order lifecycle. These omissions can materially change exits, fills and PnL. |
| Execution | Uses supplied next-observable BBO quote and displayed top-level size; OHLC bars trigger intents but do not themselves create fills. | Best bid/ask is not full depth. Actual latency, queue priority, market impact, exchange matching, protective-order trigger behavior and exchange-confirmed partial fills remain unmodeled. |
| Risk gates | Applies independent virtual gates using registered/default values. | Several live values depend on runtime environment, wallet and persisted state. They have not been fully captured/reconciled against a non-secret runtime snapshot. |

**Interpretation rule:** A can test the frozen signal-source hypothesis and the behavior of this virtual accounting model. It cannot be labelled “live parity”, a live backtest, or a replica of live profitability. Until the differences above are resolved, report its PF, drawdown and returns as *virtual counterfactual metrics only*.

## Virtual live-entry gates and their parity limits

Each A/B/C portfolio independently applies the following counterfactual gates before new entries:

| Gate | Virtual rule | Live source / parity |
|---|---|---|
| MAX_OPEN | 5 open positions | Matches live_arm.py |
| Session | MSK weekday/weekend fat/normal/thin/off schedule; off blocks new entries in this replay | Schedule/presets copied from live_session.py. **Runtime parity is unknown:** live_session.session_enabled() defaults OFF unless ZEUS_SESSION_PROFILE is set; the inspected bot.yml does not set it. Force/lock/env overrides are not modeled. Replay currently applies the schedule as a pre-registered research gate, not as proof of the live runtime setting. Capture an approved non-secret env/config snapshot before comparing absolute baseline results. |
| Cooldown | 20 minutes per strategy/symbol/side | Matches live_arm.py constant |
| Same-side cap | Block when 4 positions already have same side | Matches hard cluster max 4; not a model of every exchange-side UPNL detail |
| Same symbol | One open position per symbol | Models the open-position conflict. Live also blocks on unreconciled persisted plans even when no open position is visible; that plan-reconciliation state is not present in this public tape and remains unmodeled. |
| INJ reserve | Block INJ when pre-entry open count > 3 at MAX_OPEN=5 / reserve=2 | Matches inj_slot_blocks |
| Risk ladder | confidence >= .88: 3%; >= .80: 2%; >= .70: 1.5%; otherwise 1% | Matches conf_risk_pct / live_arm.py defaults |
| Heat | After 3 complete consecutive losing positions, halve next risk tier until UTC day roll or a complete win | Threshold/day behavior from zeus_heat.py; live caller applies tier ×0.5 |
| Daily / weekly loss | 4% / 6% of virtual cash equity using modeled cash flows | Thresholds match live_risk.py; treatment of null/missing PnL, resets and live-wallet equity differ |
| Virtual HALT | n >= 5 complete closed positions, PF < 1 over last 20 complete positions | Threshold/window match; live uses kill epoch/window and can count partial_fill rows separately and skip scratched exits. Virtual position-level accounting is intentionally different |
| Leverage / notional | 6x notional cap; 50,000 USDT per-position value default | 6x is the documented workflow forced leverage; 50K is live_arm_wiring.max_position_value_usdt() default. Runtime env override is unknown and is not imported |
| Instrument quantity filters | Not yet modeled | Live resolves step size and quantizes quantity; minimum quantity/notional, precision and exchange-specific rules remain a blocker |

The session gate is currently enabled in the virtual portfolios by default to test the registered MSK schedule; because the inspected bot workflow does not set ZEUS_SESSION_PROFILE and the arm itself is separately gated by ZEUS_ARM, this must be reconciled with the actual runtime env before interpreting A as a live-parity control. The virtual cash model is not the live broker's wallet/margin ledger. It does not reproduce maintenance margin, liquidation, margin mode, funding settlement collateral, cross-position correlation exposure, or every error/reconciliation gate. Daily/weekly accounting does not emulate live null-PnL imputation and manual reset/epoch files. These must not be described as exact live parity.

## Offline replay and filesystem/network boundary

scripts/zeus_shadow_replay.py replays an externally supplied JSONL event tape. Event types are signal, closed bar, funding, funding coverage and mark. It writes checkpoint state and output only to caller-specified paths outside the repository; event IDs and portfolio states survive restarts. It has no HTTP client, exchange client or public-data capture path.

deploy/shadow/compose.offline.yml defines an offline container profile:
- network_mode: none — no external network interface/default route;
- repository mounted read-only at /workspace;
- root filesystem read-only; only caller-supplied external data directory mounted writable at /data;
- UID/GID 65532, all capabilities dropped, no-new-privileges, PID/memory/CPU limits and small noexec/nosuid/nodev /tmp;
- no secret, Docker socket, host PID/network or trading-key mounts.

scripts/verify_shadow_runtime.py checks the effective running process, mounts, capabilities, NoNewPrivs, non-loopback interfaces/default route, and proves repo writes fail while /data writes succeed. **This runtime verifier has not been executed in a real Docker container in the current development environment** (Docker CLI/runtime is unavailable here). Therefore evidence so far is code/configuration-level, not a runtime attestation. It cannot prove a hostile/compromised host or Docker daemon, nor prevent an administrator from overriding the profile. The actual host must run the verifier and retain its output before an experiment.

The separate zeus_shadow_capture.py remains a manual one-shot public candle capture script, not part of the offline profile. It makes 5–10 public candle GETs per invocation depending on how often channel and 4h multi-top are silent; it has not been run. It does not capture execution quotes or funding events and cannot by itself supply a valid replay tape. The global BingX request budget shared with the working bot and archive is not proven; no capture network profile, scheduler, workflow or cron is connected.

## First A/B/C data plan — proposed only, no collection performed

No public market data has been collected by this PR. The one-shot capture script is not a replay dataset: it currently obtains candle snapshots only and does not obtain historical BBO quotes, displayed size, funding events or mark-price history. Do not run it as a substitute for the dataset below.

### Required tape

The initial comparison should use the five research symbols BTC-USDT, ETH-USDT, SOL-USDT, HYPE-USDT and INJ-USDT and a 30-calendar-day evaluation window, preceded by at least 8 days of warm-up for the 48 × 4h channel lookback. That implies a target of at least 38 calendar days of aligned history, plus any extra lead-in required by the actual signal implementation. The protocol's minimum of 50 fully closed positions per variant remains a sample-size gate; if the window does not produce that sample, report insufficient evidence rather than silently extending it.

| Dataset | Minimum fields | Why it is needed |
|---|---|---|
| Trades/price candles | 1m OHLCV for the full 38+ days; native 1h/4h candles as cross-checks; open/close timestamps, base volume, quote volume, trade count and taker volume where available. | Rebuild signal context and closed-bar stop/TP triggers. 1m bars still cannot reproduce sub-minute signal evaluation or the order of intrabar touches. |
| BBO quote tape | Every best-bid/best-ask update from signal decision through entry and exit; exchange timestamp, local first-receive timestamp, sequence/update ID, bid, ask, bid size and ask size. | Determine the first observable executable side and whether displayed size covers the simulated quantity. Missing or stale quotes mean UNKNOWN, not an assumed fill. |
| Depth (preferred) | Initial L2 snapshot plus sequenced bid/ask deltas, price and size at each level, with exchange and receive timestamps. | Measure depth beyond the top level, size impact and whether an apparent BBO fill is plausible. BBO alone does not establish full-size execution. |
| Funding | Historical funding rate per contract and settlement timestamp, funding interval, plus mark price and position quantity/notional at settlement. | Calculate modeled funding cash flow for each virtual position. A rate alone is not an actual account funding cash amount; sign, size and settlement alignment must be reconstructed and labelled modeled. |
| Mark prices | Historical mark-price candles or timestamped mark ticks, preferably 1m and at each funding settlement. | Mark-to-market equity, unrealized PnL, drawdown and funding notional. Last-trade/spot close is not a substitute for the contract mark price. |
| Contract and cost metadata | Tick size, quantity step, min quantity/notional, contract multiplier, fee tier, funding interval, symbol status and effective dates. | Avoid fills that the venue would reject and model costs/rules for the correct historical contract version. |

### Sources and what is actually established

1. **BingX public API (venue-matched market history, subject to retention verification).** The BingX-API reference documents `/openApi/swap/v3/quote/klines` with up to 1,440 bars per request, `/openApi/swap/v1/market/markPriceKlines` with up to 1,440 bars, and `/openApi/swap/v2/quote/fundingRate` with up to 1,000 settlements. All three are documented at one request/second per IP and accept historical time parameters. The reference does not publish an earliest retained timestamp, so the real history floor for each requested contract and interval remains UNKNOWN until returned metadata/data is independently checked. `/openApi/swap/v2/quote/bookTicker` is documented as a best-bid/ask-and-quantity request, but not as a historical time-series endpoint. The BingX USDT-M WebSocket bookTicker feed is real-time, pushes price and size updates (documented 200 ms), and is therefore a possible *future capture source*, not a retroactive archive.
2. **Binance public USD-M Futures archive (free cross-venue fallback).** Binance's public archive documents USD-M klines, aggregate trades and trades. Its helper documentation also lists historical USD-M mark-price, index-price and premium-index kline archives from 2020-01-01. This can supply cross-venue candle/mark-price control data, but it cannot stand in for BingX execution quotes, BingX funding settlement or order-book history. The archive documentation does not list historical BBO quote ticks or full L2 book updates. Data from Binance must be labelled Binance, never relabelled BingX.
3. **CoinAPI Flat Files (commercial candidate with a concrete coverage warning).** CoinAPI's exchange metadata lists `BINGXFTS` (BingX Futures) with `data_trade_start=2023-12-07` and `data_trade_end=2026-01-23`. This is exchange-level trade metadata, not proof that every requested quote, order-book, OHLCV or metrics channel exists for each of BTC/ETH/SOL/HYPE/INJ. The listed end date is before the current 2026-11 study window, so the currently published metadata does **not** establish fresh BingX Futures coverage for the intended period. Confirm exact exchange symbol IDs and per-dataset start/end dates before considering it.
4. **Tardis (commercial candidate, exact BingX coverage unconfirmed).** Its published docs describe downloadable historical CSV datasets for quotes, incremental L2, order-book snapshots, trades and derivative tickers; its historical access window depends on subscription interval and plan. The current public Perpetuals/Derivatives page lists monthly plans from $450 Academic, $900 Solo, $1,350 Professional and $3,500 Business; yearly billing shows four years of history for Academic/Solo/Professional and all available history for Business. The searched public exchange catalogue did not establish that BingX USDT-M is among the supported venues or that all five requested contracts have the required channels. Treat BingX coverage as UNKNOWN until Tardis supplies exchange-level date/channel details and a sample.

### Cost and availability gate

The 38-day 1m request volume can be estimated but not confirmed before data availability is checked: 38 × 1,440 = 54,720 bars per symbol per interval; at a 1,440-bar response cap this is at least 38 pages per symbol, or 190 requests for five symbols for one 1m series. Native 1h/4h, mark-price bars and funding history add requests. The documented BingX limit is one request per second per IP for the cited candle, mark-price and funding endpoints. No quote-history endpoint or guaranteed retention floor was found in the reviewed BingX API references. Do not schedule or issue these calls as part of this PR.

For CoinAPI Flat Files, the current public rate card prices BBO/Quotes at $8/GiB for the first 0.5 GiB per UTC day per SKU, then $4/GiB for the next 4.5 GiB and $2/GiB above 5 GiB. Full L2/L3/L4 order-book data starts at $8/GiB for the first 1 GiB/day/SKU, then $4/GiB and $2/GiB tiers; 50-level snapshots are more expensive. These are metered data-transfer rates, not a total quote. Tardis plan prices above are subscription prices, not confirmation of BingX symbol coverage. Before any purchase, require exact contract IDs, channels, historical date ranges, timestamps, update sequence semantics, sample files, estimated volume and total price in writing. No trial, paid plan, key, account or vendor request is initiated by this PR.

### Minimum useful analysis without a complete quote tape

A limited *signal-quality / filter study* can be done from synchronized candles and the already captured signal/zone state, without pretending it is an execution backtest:

- Apply one-event fan-out and report, per symbol and day, baseline signal count, B accept/reject reasons, C zone-veto reasons and each UNKNOWN category.
- From the first fully closed 1m bar after the signal decision, measure price-path MFE/MAE over a predeclared horizon, and classify whether only target, only stop, both or neither were touched. Same-bar stop/target ambiguity stays UNKNOWN; candle extremes are not fills.
- Keep entry execution, exit execution, funding cash, realized PnL, PF, wallet equity and drawdown as UNKNOWN wherever the required quote/size, funding or mark coverage is absent. Do not publish those as zero or infer fills from candle closes.
- Report coverage separately: percentage of signals with valid candles, BBO/size, funding and marks. Do not compare variants on different data coverage without explicitly showing the difference.

This reduced analysis can tell whether B and C remove signals and whether their remaining setups have different subsequent candle paths. It cannot establish which variant is more profitable, whether displayed liquidity would fill the order, or whether A matches the live bot. A proper profitability comparison requires the reconciled quote/depth, funding, mark and live-parity inputs above.

## Remaining blockers before any experiment

1. A reviewed public quote-tape source with timestamp/receive-time synchronisation, displayed sizes and a proven request budget shared with all BingX traffic.
2. Verified funding-rate/settlement history and reconciled per-position funding coverage.
3. Full live exit-engine replay, including stop adjustments, runner handling, forced exits and exchange protective-order behavior, or an explicit owner-approved simplification protocol.
4. Approved evidence of runtime ZEUS_ARM / ZEUS_SESSION_PROFILE / session overrides and persisted-plan reconciliation state; instrument filters, margin/liquidation, actual live risk equity and exact reset/epoch/partial-PF semantics.
5. Successful current CI, plus runtime execution of the isolation verifier on the intended host and retained attestation.
6. Separate explicit owner permission to begin the 30-calendar-day experiment. Minimum 50 fully closed positions per variant; otherwise report insufficient sample and do not extend automatically.

No public candle collection, replay experiment, live order, or merge has been performed by this PR work. The intended 30-day / 50 closed-position rule is only a future protocol, not a start date or guarantee.

## Tests

Synthetic tests cover one-event A/B/C fan-out and B's no-fallback rule; boundary direction and 1% threshold; zone freshness/UNKNOWN; quote selection after signal; bid/ask side and missing size; session/cooldown/MAX_OPEN/side/INJ/heat/day-week/virtual-HALT gates; stop gaps, simultaneous stop/TP touch, TP limit marketability and partial fills; missing/reconciled funding; fees, initial-risk R, closed/open PF and marked drawdown; restart/idempotency; no fabricated fills; and static sandbox configuration. They prove these synthetic software rules only — not exchange fills, global request quota or actual host isolation.
