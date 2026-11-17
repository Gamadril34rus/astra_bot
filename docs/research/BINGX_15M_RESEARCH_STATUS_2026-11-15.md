# BingX 15m strategy research status — 2026-11-15

## Scope and safety

- Venue: BingX USDT-M perpetual swaps only.
- Decisions: closed 15m candles; context: latest fully closed 1h candle.
- No 1m/5m signal logic, spot, scalping, production registration, VST or REAL routing.
- Entry is the next 15m open; intended median hold is about 30 minutes; hard maximum is 9 hours.
- A regime router may observe all regimes, but it must remain flat when no module has positive net edge after costs.

## Verified market data

The 365-day archive covers **2025-11-13 00:00 UTC through 2026-11-13 00:00 UTC (exclusive)** for BTC, ETH, SOL, HYPE and INJ.

- BingX swap kline source: `/openApi/swap/v3/quote/klines`, 1m archive run [43589812031](https://github.com/Gamadril34rus/astra_bot/actions/runs/43589812031), artifact `14114080300`.
- Per symbol: 525,600 1m rows, 35,040 merged 15m rows and 8,760 merged 1h rows; zero missing and zero duplicate 1m timestamps.
- The 1m series is used only to construct/validate 15m and 1h bars. Signals are not evaluated on 1m or 5m.
- Native BingX 15m/1h bars override matching aggregate timestamps. The manifest records small overlap differences: 15m SOL 3 and HYPE 1; 1h BTC, ETH, HYPE and INJ 1 each. These are not silently normalized; the native bars are retained on overlap.
- BingX funding source: `/openApi/swap/v2/quote/fundingRate`, merge run [43595094141](https://github.com/Gamadril34rus/astra_bot/actions/runs/43595094141), artifact `14113769474`.
- Latest contiguous funding segment: BTC 1,095 events (8h max gap), ETH 1,476 (8h), SOL 1,310 (8h), HYPE 2,232 (4h), INJ 1,476 (8h). No funding is forward-filled or imputed.
- SHA-256 for every 1m, merged 15m, merged 1h and funding CSV is checked against its archive manifest before replay. The immutable report records the hashes.
- Quote volume, trade count, taker-buy volume, historical order book, open interest and wallet identity are unavailable in this archive. Base-asset volume is used; no order-flow claims are made.

## Official staged screen: SESSION_VWAP_RECLAIM

- PR: [#347](https://github.com/Gamadril34rus/astra_bot/pull/347), research branch `research/4h-pullback-continuation`.
- Commit: `afdd4d89f2e4b032fc9c339e6bae33ee28ed4363`.
- Workflow run: [43714681727](https://github.com/Gamadril34rus/astra_bot/actions/runs/43714681727), run number 3, success; evidence artifact `14162485372`.
- Quality gates: [43714687701](https://github.com/Gamadril34rus/astra_bot/actions/runs/43714687701), success.
- Candle archive PR checks: [43714687699](https://github.com/Gamadril34rus/astra_bot/actions/runs/43714687699), success.
- Base costs per side: taker fee 5 bps, spread proxy 1.25 bps, slippage proxy 2 bps. Stress is 1.5x. Actual BingX funding settlements are charged separately.
- Result: **0 completed trades** in both IS and validation, at base and stress. The OOS was not opened by the official workflow.
- IS: 352 raw signals; 173 failed the structural risk band, 83 failed the 1.5x cost-to-risk ceiling, 96 had target reward/risk below 1.0.
- Validation: 108 raw signals; 58 failed the structural risk band, 33 failed the cost-to-risk ceiling, 17 had target reward/risk below 1.0.
- Diagnosis: the reclaim occurs too close to session VWAP for the VWAP target to pay for the structural stop and conservative taker execution costs. This is an entry/exit geometry failure, not evidence of positive or negative trade expectancy. The module is rejected in its current form.

## Additional IS/validation-only exploratory screens

These are local research prototypes, not promotion-grade workflow artifacts. They were evaluated only on the frozen IS/validation periods; no OOS PnL was calculated. They are recorded to prevent repeating failed hypotheses.

| Hypothesis | IS base PF / trades | Validation base PF / trades | 1.5x stress PF (IS / validation) | Finding |
|---|---:|---:|---:|---|
| Prior-day sweep/reclaim, target session VWAP | 1.334 / 54 | 0.432 / 16 | 1.143 / 0.347 | Validation reverses sharply; reject |
| Same sweep, fixed 1.5R target | 0.950 / 160 | 0.490 / 45 | 0.803 / 0.403 | Negative net expectancy; reject |
| 15m compression breakout with closed 1h trend | 0.530 / 130 | 0.345 / 23 | 0.454 / 0.296 | Costs and false breaks dominate; reject |
| 15m Bollinger range reversion with closed 1h low-ADX filter | 0.636 / 183 | 0.810 / 29 | 0.544 / 0.656 | Negative net expectancy; reject |
| Funding-extreme 15m range sweep | 0.750 / 139 | 1.374 / 25 | 0.644 / 1.150 | Validation is too small and stress PF is below 1.25; reject |

The prior-day sweep/VWAP prototype had a 30-minute median IS hold, but the validation sample was only 16 trades and lost money. It is not a candidate. The funding-extreme screen improved in validation but failed IS and the stress gate. No symbol/side subset is promoted from these small samples.

## Holdout integrity

The original chronological window was split at 2026-06-20 and 2026-09-01. A separate exploratory raw-signal diagnostic traversed the 2026-09-01 to 2026-11-13 period. Although it did not calculate PnL, that historical holdout is now **retired** and must not be used for candidate selection or promotion.

The runner is being hardened so that:
- IS remains 2025-11-13 to 2026-06-20.
- Validation remains 2026-06-20 to 2026-09-01.
- 2026-09-01 to 2026-11-13 is quarantined.
- A fresh OOS starts 2026-11-13 and is not opened until at least 90 days of new BingX data exist, and only after IS/validation pass.

## Next action

Continue generating new hypotheses on IS/validation only. Keep trend continuation, compression breakout, range reversion and false-break reversal as separate modules until each demonstrates positive net expectancy under 1.5x costs. Combine only modules that independently survive the validation screen; otherwise the router stays flat. Do not merge this research PR or activate any strategy.