# SESSION_VWAP_RECLAIM: BingX 15m research hypothesis

Status: implemented as an isolated research candidate; no historical result yet. Not selected, not registered, and not enabled.

## Venue and data

- BingX USDT-M perpetual swaps only.
- Decisions use completed 15m bars; entry proxy is the next 15m open.
- Context is the latest fully closed 1h bar, joined backward on the 15m close timestamp.
- The 1m source archive is used only to construct exact 15m and 1h OHLCV bars. No 1m/5m signals or trades.
- Historical funding comes from BingX funding settlements. No forward-fill or imputation.
- BingX public archive evidence currently does not provide historical taker-buy volume, trade count, order book, historical open interest or wallet identity. This is a price/volume auction proxy, not an order-flow or named-player strategy.

## Frozen hypothesis

The module looks for a mean-reversion opportunity after price stretches away from the UTC-session VWAP and then begins to reclaim it in a weak-trend 1h regime.

- VWAP: cumulative typical price (high + low + close) / 3, weighted by base-asset volume from 00:00 UTC. Only completed 15m bars are included.
- Volatility: 15m ATR14.
- Regime: latest closed 1h ADX14 must be <=20.
- Participation: current 15m base volume must be at least the mean of the previous 20 closed 15m bars. The signal bar is excluded from the denominator.
- LONG: previous 15m close was at least 1.25 ATR below session VWAP; current 15m close reclaims to 0.15-0.75 ATR below VWAP and closes bullish.
- SHORT: symmetric condition above VWAP, with a bearish current bar.
- Entry: next 15m open.
- Stop: beyond the three-bar swing by 0.15 ATR.
- Target: fixed session VWAP measured at signal close. Reject if the next-open reward/risk is below 1.0R.
- One open position per symbol. No signal means NO_TRADE.

## Exits and costs

- Stop-first OHLC execution when stop and target are both touched in one bar.
- No-progress exit after 30 minutes if MFE remains below 0.35R.
- No-progress exit after 90 minutes if MFE remains below 0.75R.
- Hard maximum holding time: 9 hours.
- Base execution-cost proxy per side: 5 bps taker fee, 1.25 bps spread, 2 bps slippage.
- Stress: 1.5x each component. Funding remains the actual BingX settlement contribution.
- Spread and slippage are assumptions, not historical exchange fills. Results are reported per 1,000 USDT notional as a normalization, not as a position-size recommendation.

## Staged validation

1. Require a common, gap-checked 365-day 15m candle window and exact matching funding coverage for BTC, ETH, SOL, HYPE and INJ. Each symbol must pass its own funding cadence and edge checks before joining the promotion cohort.
2. Run IS and validation as separate flat simulations. Exclude every trade that crosses a split boundary.
3. Open OOS only if both IS and validation each have at least 30 trades, PF >=1.0 and positive net expectancy at both base and 1.5x costs.
4. OOS pass requires net PF >=1.25, positive net expectancy, at least 100 combined trades, >=70% positive calendar months, positive 1.5x stress PF and expectancy, realized equal-notional drawdown no worse than -10%, and trade-sequence drawdown no worse than -20%.
5. Require at least 25 OOS trades per side before making a side-specific claim. Otherwise report side sample as insufficient.
6. Report gross return, taker fees, spread, slippage, funding and net return separately, with trade count, PF, expectancy, win rate, holding distribution, positive-month share, drawdown and concurrent exposure by symbol and side.

## Holdout and safety

The OOS replay starts flat at its own boundary and is not simulated or summarized unless the IS/validation gate passes. IS, validation and OOS are simulated independently; boundary-crossing trades are excluded. The OOS is not a tuning set. A failed candidate is diagnosed using IS/validation only, and the same OOS remains sealed for subsequent hypotheses.

This is research-only. It does not change production strategy registration, live Zeus execution, VST routing, REAL routing, risk, leverage, sizing, production stops or the production exit engine. Even a historical pass would only make the candidate ready for owner review and separate VST planning; it would not guarantee future profits.
