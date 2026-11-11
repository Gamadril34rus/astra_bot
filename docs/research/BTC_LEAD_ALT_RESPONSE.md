# BTC-led altcoin response: isolated research hypothesis

Status: research-only, unvalidated, not registered in the live strategy engine.

## Hypothesis

A fully closed BTC hourly impulse may be followed by a delayed response in an altcoin that has under-reacted to the BTC move relative to its recent hourly beta. The candidate enters in the direction of the BTC impulse only when both the BTC shock and the signed altcoin under-reaction are unusually large.

This is motivated by published evidence of delayed BTC-to-altcoin price transmission, especially in less liquid assets. The source study uses one-minute data; this implementation deliberately tests a much slower and more executable 15-minute decision process. It is not assumed that the published effect survives that change of horizon.

## Exact signal definition

- Signal timeframe: closed 15-minute BingX USDT-M perpetual candles.
- Context: the most recent fully closed 1-hour BTC and target-asset candles, joined backward by candle decision time.
- A signal is evaluated only on the 15-minute candle whose decision time exactly matches the newly closed BTC 1-hour candle. This prevents repeated entries from the same hourly event.
- Hourly log returns are used.
- BTC shock: absolute current BTC hourly return divided by the standard deviation of the prior 168 hourly BTC returns. Require at least 120 prior observations and a z-score of at least 1.5.
- Rolling beta: covariance of prior 168 target-asset and BTC hourly returns divided by prior BTC hourly-return variance. Current-hour returns are excluded from beta estimation. Require beta >= 0.20.
- Under-reaction: signed residual is sign(BTC return) * (beta * BTC return - altcoin return). Divide by the standard deviation of the prior 168 residual observations. Require a z-score >= 0.75.
- Direction: long after a positive BTC impulse; short after a negative BTC impulse.
- BTC itself is never traded as a target. It is the leader series only.

## Execution and risk assumptions

- Signal at 15-minute close; entry at the next 15-minute open.
- Initial stop: 1.2 signal-bar ATR from the signal close.
- Target: 1.5R.
- Conservative OHLC ordering: if stop and target are both touched in one bar, stop is assumed first.
- Existing research simulator applies explicit fees, spread, slippage, funding, a 30-minute no-progress exit, a 90-minute progress check, and a hard 9-hour maximum hold.
- Base and 1.5x stressed costs must be reported on the same signal population.
- This is an OHLC proxy, not a reconstruction of actual fills or liquidation prices.

## Anti-leakage controls

- BTC and altcoin H1 features are merged only by fully closed decision_time.
- The 15-minute event flag is true only at the exact H1 close boundary.
- Beta and BTC volatility use prior hourly returns only.
- Residual volatility uses prior residuals only.
- Entry is delayed until the next 15-minute bar open.
- Chronological IS / validation / OOS boundaries and split-crossing trade exclusion remain enforced by the shared research simulator.

## Required evidence before any promotion discussion

- Verify provenance, gaps, SHA-256 and common BingX history for all source files.
- Independently replay this family alone; do not rely on the mixed-family router.
- Report BTC shock count, under-reaction count, signals, rejected trades, completed trades, and breakdown by symbol and direction.
- Report net PF, net expectancy, win rate, holding distribution, positive OOS months, drawdown proxies, concurrent exposure, and separate fee / spread / slippage / funding contributions.
- Run base and 1.5x cost cases.
- Require at least 12 months of common history, at least 100 combined OOS trades and 25 per direction before side-specific claims, OOS net PF >= 1.25, positive net expectancy, at least 70% positive OOS calendar months, acceptable drawdown, and positive net expectancy under 1.5x costs.
- If sample size or any gate fails, classify as inconclusive or rejected. Do not tune on OOS.

## Known limitations and falsification risks

- The motivating study is based on 1-minute data; a 15-minute signal may be too slow to capture the effect.
- Large and medium-cap assets may react almost immediately, leaving little exploitable lag after realistic costs.
- Under-reaction can represent asset-specific negative information rather than delayed price discovery.
- Candle OHLCV does not contain order-book depth, liquidation prints, or full aggressor-side flow.
- BTC/altcoin lead-lag can be regime-dependent and unstable.
- A positive in-sample result is not sufficient. OOS and stressed-cost failure rejects the hypothesis.

No production code, strategy registration, VST, REAL routing, leverage, or risk settings are changed by this research module.
