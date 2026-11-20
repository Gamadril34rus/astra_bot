# Crowd Liquidity Trap (CLT): research specification

**Status:** hypothesis and event-label contract only. No strategy edge is established.  
**Owner scope:** independent research; separate from Zeus DEMO/VST and all production execution.

## Research question

Does a repeatable sequence predict a 30-minute to 9-hour move on BingX USDT-M perpetuals?

1. A broad cohort of smaller participants materially increases LONG or SHORT inventory.
2. After that build begins, a separately classified large-participant cohort materially increases the opposite inventory. Closing an existing position is not an opening/addition.
3. In a later 15m bucket, the crowd continues adding after the large opposite addition; same-bucket ordering is not assumed.
4. Before the break candle, price makes weak progress in the crowd's direction relative to prior-only volatility (absorption proxy).
5. A subsequent completed 15m candle breaks the relevant prior swing structure, with closed 1h context available only as a filter/regime descriptor.
6. Entry is at the next 15m bar open. No prediction of a whale entry and no intrabar look-ahead.

The SHORT setup is: crowd net LONG inventory rises, large cohort net SHORT inventory rises, upward price progress stalls, then a closed 15m candle breaks below prior structure. LONG is the exact mirror.

## Inventory accounting contract

For each wallet, market and 15m bucket, reconstruct inventory changes from the fill's position immediately before execution:

- LONG inventory delta = Open/Add Long notional minus Close Long notional.
- SHORT inventory delta = Open/Add Short notional minus Close Short notional.
- A fill that crosses through zero must be split into the closing portion and the newly opened opposite portion.

A buy can open/add LONG or close SHORT. A sell can open/add SHORT or close LONG. The Hyperliquid user-fill schema exposes `startPosition`, `side`, `sz`, `px`, and a `dir` label; use startPosition + side + size to split the fill, then cross-check the derived action against `dir`. Seed the first observed fill from its own startPosition; do not assume the account was flat at the beginning of the archive. Verify that each next fill's startPosition matches the previous reconstructed position. Any mismatch, missing field, unknown direction label, API history cap, TWAP cap, or unresolved page-boundary collision invalidates that wallet-market series rather than imputing inventory.

The public Info API is bounded: userFillsByTime returns at most 2,000 fills per response and exposes only the 10,000 most recent fills per address; TWAP slice fills are a separate endpoint with its own 2,000-row ceiling. These limits mean API history coverage must be proven per wallet before using its position series. Paired public trade prints alone remain insufficient for exact inventory reconstruction.

## Participant cohorts

Do not hard-code wallet addresses or classify a wallet as a whale from one large fill. Build rolling, market-specific participant size distributions using prior-only data. Candidate classifications are small cohort and large cohort based on rolling account/position exposure quantiles, with minimum active-wallet and concentration requirements. Thresholds are selected on IS, frozen for Validation and untouched OOS. Report cohort churn and sensitivity to percentile cutoffs.

A public archive that contains paired trade prints but lacks order ID and explicit Open/Close direction cannot support this inventory reconstruction. It can support aggressor-flow controls only. Do not relabel those controls as CLT.

## Event features

All values are causal and available at the close of the event bar:

- crowd_long_inventory_delta_usd, crowd_short_inventory_delta_usd
- large_long_inventory_delta_usd, large_short_inventory_delta_usd
- crowd directional additions and opposing large-cohort additions over a bounded sequence window
- pre-break price progress in the crowd's direction, normalized by prior-only 15m volatility over elapsed bars; retain crowd notional to test progress-per-dollar as a separate feature
- 15m swing high/low calculated from bars strictly before the event bar
- closed 1h context; no unfinished 1h candle
- funding, spread and slippage inputs for execution replay

The time between crowd build and opposing large additions is measured from the training sample. Do not assume a fixed 15/30-minute delay.

## Execution and validation

- Venue for the eventual execution replay: BingX USDT-M perpetuals.
- Signal bars: 15m; context: closed 1h. 1m may only aggregate execution/cost data.
- Entry: next 15m open after the structure-break close.
- Holding window: evaluate 30m through 9h; no scalping.
- Include actual funding where available and conservative fees, spread and slippage. Stress at 1.5x costs.
- Use non-overlapping events and day/market-clustered uncertainty estimates.
- Split chronologically into IS, Validation and untouched OOS/walk-forward. Tune only on IS; freeze before Validation/OOS.
- Controls/ablations: price-only structure break; crowd-only; large-opposite-flow only; aggressor-side-only proxy; simple large-wallet-follow baseline; remove absorption; remove structure confirmation; shuffled event times.
- Report both directions, each market, regimes, trades, expectancy, PF, drawdown, cost sensitivity, sample count and confidence intervals.
- Minimum promotion screen: at least 100 OOS trades, PF >= 1.25, positive expectancy at 1.5x costs, no single market/regime carrying the entire result. This is a research gate, not a promise of profitability.

## Current data limitation (do not bypass)

The frozen 37-day Ironflow paired-trade archive has wallet addresses and aggressor side, but no order ID, explicit position direction, fees or realized PnL. It cannot establish whether a wallet opened/added or closed/reduced a position. The recent 24h public-API audit covers only a selected subset and is an interpretation check, not historical reconstruction. The earlier 32-event BingX flow replay is not a CLT backtest and its candidate-minus-control interval crossed zero.

Therefore the first gate is **historical inventory-data feasibility**. Until a complete, auditable historical position-direction series is available, the model may be tested on synthetic fixtures and aggressor-flow controls only. No CLT performance claim, parameter tuning, or strategy promotion from those proxies.


## Historical inventory feasibility pilot

A research-only pilot is being added for the frozen 2026-10-03 through 2026-11-08 cohort. It selects only addresses whose manifest fill count is at or below the 10,000-fill API ceiling and whose top markets include one of BTC/ETH/SOL/HYPE/INJ. It retrieves direction-aware fills and TWAP slices, validates startPosition continuity, and builds 15m inventory deltas. The first 7 days are reserved for a prior-only position-size calibration; later bars are used for event discovery.

The pilot's small and large groups are relative to the selected leaderboard sample (lower half vs upper quartile of median position notional), not a market-wide retail/whale census. It is a feasibility diagnostic only. If too few continuous wallet-market histories remain, the correct result is insufficient data, not a weakened completeness gate.
