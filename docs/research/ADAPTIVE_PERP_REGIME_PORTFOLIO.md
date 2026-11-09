# Adaptive BingX USDT-M perpetual portfolio — research contract

Status: frozen research hypotheses; not validated and not enabled.

## Owner constraints
- BingX USDT-M perpetual contracts only. No spot and no scalping.
- Target holding time around 30 minutes; hard maximum 9 hours.
- Symbols: BTC-USDT, ETH-USDT, SOL-USDT, HYPE-USDT, INJ-USDT.
- No requirement to force trades. A regime with no positive net edge must produce NO_TRADE; forcing activity would convert fees into a permanent tax.

## Research objective
Test whether a simple regime router can combine four distinct short-horizon families without double-counting signals:
1. TREND_PULLBACK: 1h directional trend + 15m pullback/reclaim.
2. COMPRESSION_BREAKOUT: 15m volatility compression + range break and relative volume confirmation.
3. FALSE_BREAK_REVERSAL: failed 20-bar range escape, return inside range, and weak 1h trend.
4. RANGE_REVERSION: weak 1h trend + 15m Bollinger re-entry from an extreme.

The router assigns each closed 15m decision bar to at most one family, in priority order COMPRESSION_BREAKOUT, FALSE_BREAK_REVERSAL, TREND_PULLBACK, RANGE_REVERSION. If no family has a valid signal, it does not trade. One open position per symbol.

## Frozen v0 rules
### Shared
- 15m signal candle must be closed; entry proxy is the next 15m open.
- 1h context is the latest 1h candle whose close timestamp is <= the 15m decision timestamp. No forming candles.
- ATR14 is rolling mean true range; EMA values use pandas ewm(span, adjust=False); RSI14 uses Wilder-style ewm.
- No-progress rule: after 2 completed 15m position bars (30m), exit at that bar close if maximum favorable excursion is still below 0.35R. This is an invalidation rule, not a forced 30m take-profit.
- Every trade has a 36-bar (9h) hard maximum hold.
- If stop and target are touched in the same OHLC bar, stop wins. If a stop is gapped through, use the adverse bar open.
- No pyramiding, averaging down, grid, martingale, same-symbol re-entry before exit, or simultaneous opposite positions.

### TREND_PULLBACK
- Regime: 1h ADX14 >= 20; EMA20 > EMA50 and EMA20 rising over 3 closed bars for LONG; symmetric for SHORT.
- Setup: within the prior 4 closed 15m bars, price tests EMA20 while closes remain on the trend side of EMA50.
- Trigger: current closed 15m candle reclaims EMA20, closes beyond prior candle high/low, and quote-volume ratio >= 1.1.
- Stop: beyond prior 4-bar swing by 0.15 ATR; reject risk outside 0.35–1.8 ATR.
- Target: 1.5R.

### COMPRESSION_BREAKOUT
- Setup: prior 15m Bollinger bandwidth percentile over trailing 96 bars <= 25%.
- Trigger: current close breaks the prior 20-bar high/low and quote-volume ratio >= 1.5.
- 1h EMA20/EMA50 direction must not oppose the breakout.
- Stop: 1.2 ATR; target: 1.5R.

### FALSE_BREAK_REVERSAL
- Regime: 1h ADX14 <= 22.
- Setup: the prior closed 15m candle closes outside the preceding 20-bar range on relative quote volume >= 1.5.
- Trigger: the current closed 15m candle closes back inside that same pre-break range.
- Stop: beyond the failed-break excursion by 0.15 ATR.
- Target: midpoint of the pre-break range; reject if reward/risk < 1.0.

### RANGE_REVERSION
- Regime: 1h ADX14 <= 18.
- LONG: prior close below lower Bollinger band, current close re-enters above lower band, RSI14 <= 40.
- SHORT: prior close above upper band, current close re-enters below upper band, RSI14 >= 60.
- Stop: beyond prior 3-bar swing by 0.15 ATR.
- Target: 15m EMA20 at signal time; reject if reward/risk < 1.0.

## Costs and funding
- Base taker fee: 5 bps per side; conservative spread proxy: 2 bps per side; adverse slippage proxy: 3 bps per side (20 bps round trip combined).
- Stress all execution costs at 1.5x (15 bps per side, 30 bps round trip). Signal eligibility is frozen to the stress model: reject if round-trip stress cost exceeds 0.40R. This keeps the base and stress runs on the same trade population.
- Funding is charged only at actual settlement timestamps crossed by the position, using signed symbol-specific BingX funding rates. Positive funding rate is paid by LONG and received by SHORT. Missing funding coverage is INCOMPLETE, never zero.
- Gross return, taker fees, spread, slippage, funding and net return are separate ledger columns.
- Funding gaps >12h are not imputed. For a symbol with older funding holes, only its latest uninterrupted funding segment is eligible; earlier candle bars are excluded and the excluded funding count/window is reported.
- OHLC fills remain a proxy. A later VST replay must use exchange-confirmed fills and fees.

## Validation
- Source must be BingX USDT-M swap klines, not spot and not Binance substitute data.
- Validate OHLCV, duplicates, gaps, symbol coverage, timestamps, contract listing windows, funding intervals and SHA-256 checksums recorded in both archive manifests.
- Chronological periods: IS through 2023-12-31; validation 2024; OOS from 2025-01-01. HYPE's shorter listing history is reported separately and is not pooled into pre-listing periods.
- No parameter search on OOS. Compare each family, each side, each symbol, and the router.
- Report PF, net expectancy, win rate, median/mean/P90 holding time, monthly positive share, drawdown, exposure and concurrent positions.
- A small sample is INSUFFICIENT, not PASS.
- Research promotion gate: OOS net PF >= 1.25; positive net expectancy; >=100 OOS trades combined; >=25 per side before side claims; positive months >=70%; drawdown within owner risk budget; net positive under 1.5x costs. Then require separate VST forward validation.
- A profitable historical result is not a promise of future returns and does not authorize live activation.

## Safety
This research branch must not change active strategy registration, production risk, leverage, stops, exit engine, paper/real routing, secrets, workflows unrelated to research, or live settings.


## First completed data replay: rejected, not promoted

The first full BingX replay completed at the original cost assumptions but failed the promotion gates. It produced 135 trades over the common 15m data window 2026-08-01 through 2026-11-09, less than 12 months. Base-cost combined PF was 0.6955 with net expectancy -0.19618% per trade; the 1.5x cost case PF was 0.5834 and net expectancy -0.29618%. The worst-performing families were COMPRESSION_BREAKOUT (PF 0.4373) and FALSE_BREAK_REVERSAL (PF 0.3860). TREND_PULLBACK (PF 1.0288) and RANGE_REVERSION (PF 1.0048) were close to flat, not proven edges. This result is retained as a failed baseline and is not a reason to tune parameters against the same short sample.
