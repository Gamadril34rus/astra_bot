# Adaptive BingX USDT-M perpetual portfolio — research contract

Status: frozen research hypotheses; not validated and not enabled.

## Owner constraints
- BingX USDT-M perpetual contracts only. No spot and no scalping.
- Target holding time around 30 minutes; hard maximum 9 hours.
- Symbols: BTC-USDT, ETH-USDT, SOL-USDT, HYPE-USDT, INJ-USDT.
- No requirement to force trades. A regime with no positive net edge must produce NO_TRADE; forcing activity would convert fees into a permanent tax.

## Research objective
Test whether a simple regime router can combine four distinct short-horizon families without double-counting signals:
1. TREND_PULLBACK: 1h directional trend + 30m pullback/reclaim.
2. COMPRESSION_BREAKOUT: 30m volatility compression + range break and relative volume confirmation.
3. FALSE_BREAK_REVERSAL: failed 20-bar range escape, return inside range, and weak 1h trend.
4. RANGE_REVERSION: weak 1h trend + 30m Bollinger re-entry from an extreme.

The router assigns each closed 30m decision bar to at most one family, in priority order COMPRESSION_BREAKOUT, FALSE_BREAK_REVERSAL, TREND_PULLBACK, RANGE_REVERSION. If no family has a valid signal, it does not trade. One open position per symbol.

## Frozen v0 rules
### Shared
- 30m signal candle must be closed; entry proxy is the next 30m open.
- 1h context is the latest 1h candle whose close timestamp is <= the 30m decision timestamp. No forming candles.
- ATR14 is rolling mean true range; EMA values use pandas ewm(span, adjust=False); RSI14 uses Wilder-style ewm.
- No-progress rule: after 1 completed 30m position bar (30m), exit at that bar close if maximum favorable excursion is still below 0.35R. If the position remains open after 3 bars (90m) and MFE is still below 0.75R, exit at that close. These are invalidation rules, not forced profit targets.
- Every trade has a 18-bar (9h) hard maximum hold.
- If stop and target are touched in the same OHLC bar, stop wins. If a stop is gapped through, use the adverse bar open.
- No pyramiding, averaging down, grid, martingale, same-symbol re-entry before exit, or simultaneous opposite positions.

### TREND_PULLBACK
- Regime: 1h ADX14 >= 20; EMA20 > EMA50 and EMA20 rising over 3 closed bars for LONG; symmetric for SHORT.
- Setup: within the prior 4 closed 30m bars (2h), price tests EMA20 while closes remain on the trend side of EMA50.
- Trigger: current closed 30m candle reclaims EMA20, closes beyond prior candle high/low, and quote-volume ratio >= 1.1.
- Stop: beyond prior 4-bar swing by 0.15 ATR; reject risk outside 0.35–1.8 ATR.
- Target: 1.5R.

### COMPRESSION_BREAKOUT
- Setup: prior 30m Bollinger bandwidth percentile over trailing 96 bars <= 25%.
- Trigger: current close breaks the prior 20-bar high/low and quote-volume ratio >= 1.5.
- 1h EMA20/EMA50 direction must not oppose the breakout.
- Stop: 1.2 ATR; target: 1.5R.

### FALSE_BREAK_REVERSAL
- Regime: 1h ADX14 <= 22.
- Setup: the prior closed 30m candle closes outside the preceding 20-bar range on relative quote volume >= 1.5.
- Trigger: the current closed 30m candle closes back inside that same pre-break range.
- Stop: beyond the failed-break excursion by 0.15 ATR.
- Target: midpoint of the pre-break range; reject if reward/risk < 1.0.

### RANGE_REVERSION
- Regime: 1h ADX14 <= 18.
- LONG: prior close below lower Bollinger band, current close re-enters above lower band, RSI14 <= 40.
- SHORT: prior close above upper band, current close re-enters below upper band, RSI14 >= 60.
- Stop: beyond prior 3-bar swing (90m) by 0.15 ATR.
- Target: 30m EMA20 at signal time; reject if reward/risk < 1.0.

## Costs and funding
- Base taker fee: 5 bps per side; book-crossing spread proxy: 1.25 bps per side; adverse slippage proxy: 2 bps per side (16.5 bps round trip combined).
- Stress all execution costs at 1.5x (7.5 bps fee + 1.875 bps spread + 3 bps slippage per side; 24.75 bps round trip). Signal eligibility is frozen to the stress model: reject if round-trip stress cost exceeds 0.40R. This keeps base/stress runs on the same trade population. Spread proxy is set slightly above the widest current $10k round-trip book snapshot observed (INJ: 2.439 bps); these are still estimates and require VST verification.
- Funding is charged only at actual settlement timestamps crossed by the position, using signed symbol-specific BingX funding rates. Positive funding rate is paid by LONG and received by SHORT. Missing funding coverage is INCOMPLETE, never zero.
- Gross return, taker fees, spread, slippage, funding and net return are separate ledger columns.
- Funding gaps >12h are not imputed. For a symbol with older funding holes, only its latest uninterrupted funding segment is eligible; earlier candle bars are excluded and the excluded funding count/window is reported.
- OHLC fills remain a proxy. A later VST replay must use exchange-confirmed fills and fees.

## Validation
- Source must be BingX USDT-M swap klines, not spot and not Binance substitute data.
- Validate OHLCV, duplicates, gaps, symbol coverage, timestamps, contract listing windows, funding intervals and SHA-256 checksums recorded in both archive manifests.
- Chronological periods for the 30m replay: common history is split 60% IS / 20% validation / 20% OOS. The common window is bounded by the latest first timestamp and earliest last timestamp across all five instruments. HYPE shortens the common window and is not pooled into pre-listing periods.
- No parameter search on OOS. Compare each family, each side, each symbol, and the router.
- Report PF, net expectancy, win rate, median/mean/P90 holding time, monthly positive share, drawdown, exposure and concurrent positions.
- A small sample is INSUFFICIENT, not PASS.
- Research promotion gate: at least 12 months of common 30m history across all five symbols; OOS net PF >= 1.25; positive net expectancy; >=100 OOS trades combined; >=25 per side before side claims; positive months >=70%; mean hold <=90m, median <=60m, max <=9h; drawdown within owner risk budget; net positive under 1.5x costs. Then require separate VST forward validation.
- A profitable historical result is not a promise of future returns and does not authorize live activation.

## Safety
This research branch must not change active strategy registration, production risk, leverage, stops, exit engine, paper/real routing, secrets, workflows unrelated to research, or live settings.


## First completed data replay: rejected, not promoted

The first full BingX replay completed at the original cost assumptions but failed the promotion gates. It produced 135 trades over the common 15m data window 2026-08-01 through 2026-11-09, less than 12 months. Base-cost combined PF was 0.6955 with net expectancy -0.19618% per trade; the 1.5x cost case PF was 0.5834 and net expectancy -0.29618%. The worst-performing families were COMPRESSION_BREAKOUT (PF 0.4373) and FALSE_BREAK_REVERSAL (PF 0.3860). TREND_PULLBACK (PF 1.0288) and RANGE_REVERSION (PF 1.0048) were close to flat, not proven edges. This result is retained as a failed baseline and is not a reason to tune parameters against the same short sample.


## 30m archive finding and next replay

The first archive attempt on 2026-11-09 added 30m data but failed while downloading INJ 15m candles: BingX repeatedly returned API code 100410 (rate limit / cooldown) and the original downloader wrote a symbol/timeframe file only after all pages completed. The uploaded partial artifact still contains uninterrupted 30m and 1h rows for BTC, ETH, SOL and HYPE, with zero detected candle gaps:
- BTC 30m: 2025-12-06 00:00 UTC through 2026-11-09 18:30 UTC.
- ETH 30m: 2025-07-09 00:00 UTC through 2026-11-09 18:30 UTC.
- SOL 30m: 2025-11-03 08:00 UTC through 2026-11-09 18:30 UTC.
- HYPE 30m: 2026-03-06 00:00 UTC through 2026-11-09 18:30 UTC.
- All four 15m files in that partial artifact start only 2026-08-01 14:00 UTC. This is why 15m was not a suitable primary research timeframe for the current common sample.

The research runner is being moved to 30m signals with 1h closed-candle context. Holding rules are expressed in elapsed time: invalidate after 30m if MFE <0.35R, after 90m if MFE <0.75R, and hard-exit at 9h. IS/validation/OOS use a chronological 60/20/20 split of the common 30m window. This remains an exploratory sample, not a promotion pass; common coverage with HYPE is under 12 months.

Downloader hardening: lower request pace, wait a full five-minute cooldown on 100410, persist every verified page, and resume partial contiguous archives with an overlap. The next replay must complete all five symbols, both 30m and 1h, and pass gap/funding validation before any PnL is interpreted.


## Full five-symbol 30m replay completed: current router rejected

Source: GitHub Actions run #92, run ID 42692378936, artifact `adaptive-bingx-perpetual-research`, artifact SHA-256 `7fc263ac15ba4bd2058d41ca63bb41d50d70ffc12d35f90b7e4204e2e45f2fe1`. The job completed successfully, including funding download, 30m/1h candle download, base replay, 1.5x stress replay and immutable artifact upload.

### Archive coverage and integrity

All five BingX USDT-M perpetual symbols were present on both 30m and 1h. Each candle file reported zero gaps. Common interval is 2026-04-05 00:00 UTC through 2026-11-09 19:30 UTC: 218.81 days. HYPE is the limiting listing/history window. This is materially shorter than the required 12 months.

The replay excluded earlier funding history before each symbol's latest uninterrupted segment. The included funding segments were:
- BTC: 2025-07-09 08:00 UTC to 2026-11-09 16:00 UTC, max internal gap 8h.
- ETH: 2025-08-29 08:00 UTC to 2026-11-09 16:00 UTC, max internal gap 8h.
- SOL: 2025-04-12 00:00 UTC to 2026-11-09 16:00 UTC, max internal gap 8h.
- HYPE: 2026-04-05 04:00 UTC to 2026-11-09 20:00 UTC, max internal gap 4h.
- INJ: 2025-09-03 00:00 UTC to 2026-11-09 16:00 UTC, max internal gap 8h.

Older funding holes were not replaced with zero. The 60/20/20 common-window split boundaries were validation start 2026-08-14 06:54 UTC and OOS start 2026-09-27 01:12 UTC.

### Portfolio results

| Period | Trades | Base PF | Base net expectancy / trade | 1.5x PF | 1.5x net expectancy / trade |
|---|---:|---:|---:|---:|---:|
| IS | 261 | 0.5449 | -0.23319% | 0.4515 | -0.31569% |
| Validation | 81 | 0.6418 | -0.16717% | 0.5263 | -0.24967% |
| OOS | 74 | 0.5406 | -0.24751% | 0.4486 | -0.33001% |
| Combined | 416 | 0.5614 | -0.22288% | 0.4644 | -0.30538% |

Combined win rate was 26.92% at base costs and 23.32% under stress. Combined equal-notional realized drawdown proxy was -24.09% base and -32.51% stress. Median hold was 30m, mean 76.2m, P90 150m, and maximum 540m (the 9h hard cap). No period had positive months: positive-month share was 0% in IS, validation and OOS.

The OOS sample has 74 trades, below the >=100 promotion minimum. More importantly, all three periods have PF <1 and negative net expectancy. This is not a borderline or cost-only failure.

### Family breakdown

| Family | All trades | Base PF | Base net expectancy / trade | OOS trades | OOS PF | OOS net expectancy / trade |
|---|---:|---:|---:|---:|---:|---:|
| COMPRESSION_BREAKOUT | 124 | 0.6549 | -0.19001% | 21 | 0.5347 | -0.33677% |
| FALSE_BREAK_REVERSAL | 57 | 0.6105 | -0.17164% | 9 | 0.5390 | -0.19455% |
| RANGE_REVERSION | 77 | 0.4056 | -0.33304% | 18 | 0.5563 | -0.25501% |
| TREND_PULLBACK | 158 | 0.5495 | -0.21348% | 26 | 0.5344 | -0.18856% |

Every family is negative in IS/combined and OOS. No existing family is suitable for promotion based on this replay. The strongest combined family by PF is compression breakout at 0.6549, still decisively below 1.0.

### Symbol and side observations

Only ETH SHORT had slightly positive full-window base expectancy (+0.03809% across 36 trades, PF 1.1345); it fell to -0.04441% under stress (PF 0.8669). All other symbol/side groups were negative on the full window. This isolated subgroup is not a valid strategy: its OOS group has 9 trades, PF 0.9812 and expectancy -0.00447%.

### Decision

Reject the current four-family router as a candidate for VST or production. Do not tune its thresholds against this same OOS sample. The current evidence says the strategy's gross trade selection is not producing enough edge to cover execution costs and funding. The next research task should be a new, structurally distinct strategy hypothesis and longer data coverage, not cosmetic changes to this router.

This remains an OHLC replay proxy, not exchange-confirmed fills. No live or VST activation is authorized by this result.
