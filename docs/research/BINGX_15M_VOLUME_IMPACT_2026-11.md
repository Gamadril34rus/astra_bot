# BingX 15m volume-impact research (2026-11)

**Status:** exploratory IS/validation screen only. No production registration, no merge, no VST.

## Research lineage

- Parent research branch: `research/4h-pullback-continuation` (the current PR #347 research line built on the PR #343 adaptive-regime work).
- PR #341's 4h breakout-retest candidate remains rejected: combined OOS PF was about 1.00 across 102 trades.
- The 15m session-VWAP reclaim screen was also rejected: zero completed trades in both IS and validation. The main failure was stop/risk and cost-to-risk rejection, not a profitable sample.
- This branch tests a new volume/price-impact hypothesis. It does not revive or promote either rejected candidate.

## BingX data contract and limitations

The immutable source archive is the 365-day BingX USDT-M perpetual archive from workflow run [43589812031](https://github.com/Gamadril34rus/astra_bot/actions/runs/43589812031), with the matching historical funding archive from [43595094141](https://github.com/Gamadril34rus/astra_bot/actions/runs/43595094141).

Window: 2025-11-13 00:00 UTC through (exclusive) 2026-11-13 00:00 UTC.

Per symbol (BTC, ETH, SOL, HYPE, INJ):
- 525,600 1m source candles;
- 35,040 15m aggregates;
- 8,760 1h aggregates;
- zero missing or duplicate 1m bars;
- 365 days of BingX historical funding, with the common-window cadence and edge checks passing.

The 1m candles are used **only** to build 15m and 1h OHLCV aggregates. They are not signal or execution bars. All signals are evaluated at a closed 15m candle and the 1h context is backward-as-of joined on its close time.

The verified candle archive contains only OHLCV base-asset volume. It does not contain historical quote volume, trade count, taker-buy/sell volume, order-book depth, historical OI, or wallet identity. A separate attempt to fetch taker-flow fields from the 1m kline endpoint failed on run [43716252631](https://github.com/Gamadril34rus/astra_bot/actions/runs/43716252631), because the returned row did not contain the expected `quoteVolume` field. The BingX public documentation is inconsistent: the market-data skill describes a 7-field kline row, while another API reference lists additional fields. Until the live endpoint reliably returns and validates those fields, this research does **not** claim to observe aggressor flow or identify large players.

Official references:
- [BingX perpetual-futures market-data skill](https://github.com/BingX-API/api-ai-skills/blob/main/skills/bingx-perpetual-future-market/SKILL.md)
- [BingX API reference](https://github.com/BingX-API/api-ai-skills/blob/main/skills/bingx-perpetual-future-market/references/api-reference.md)

The archive manifest records one 0.1-price-unit close mismatch in the native 1h overlap (one mismatch across 8,196 overlapping bars). To avoid mixing that splice into the test, this branch uses the pure 1m-derived 15m and 1h aggregates for every symbol. The 15m native overlap was exact.

## Fixed hypotheses

1. **VOLUME_IMPACT_CONTINUATION**: 1h trend strength and EMA alignment, 1h volume expansion, then a 15m high-relative-volume range escape with a directional body and close near the candle extreme.
2. **VOLUME_ABSORPTION_RELEASE**: a high-volume 1h candle with little normalized price displacement, followed by a high-volume 15m range break.
3. **VOLUME_REGIME_ROUTER**: routes to continuation at 1h ADX >= 22, to absorption-release at ADX <= 18, and deliberately stays flat in the 18-22 neutral band. It does not force a trade.

Volume is a price/volume proxy only. It cannot establish whether the volume was buyer- or seller-initiated.

## Execution and validation policy

- 15m signal/entry decisions only; entry at the next 15m open.
- Latest fully closed 1h candle for context; no forming-hour data.
- Structural swing stop with 0.15 ATR buffer; 1.5R target.
- Existing research exit engine: no-progress checks at 30m and 90m, hard maximum 9h.
- Base costs per side: 5 bps taker fee, 1.25 bps spread, 2 bps slippage.
- Stress: 1.5x each execution-cost component.
- Actual BingX historical funding settlements are applied separately from execution costs.
- Gross PnL, fee, spread, slippage, funding, net PnL, PF, expectancy, win rate, holding distribution, positive-month share, drawdown, and concurrent positions are reported separately by module, symbol, and side.

Chronological split:
- IS: 2025-11-13 through 2026-06-19;
- validation: 2026-06-20 through 2026-08-31;
- retired historical OOS: 2026-09-01 through 2026-11-12 (not used for tuning or promotion);
- fresh OOS begins 2026-11-13. The archived dataset ends at that boundary, so fresh OOS currently has 0 days.

Preliminary screen: at least 30 trades in each IS/validation period, net PF >= 1.0, and positive net expectancy under both base and 1.5x costs.

Final historical gate remains stricter: at least 90 untouched fresh-OOS days, 100 combined OOS trades, net PF >= 1.25, positive expectancy, >=70% positive OOS months, acceptable drawdown, positive 1.5x cost stress, and at least 25 trades per side before making side-specific claims.

No candidate passing the preliminary screen is automatically enabled. A VST forward test requires separate owner approval after the independent OOS gate passes.
