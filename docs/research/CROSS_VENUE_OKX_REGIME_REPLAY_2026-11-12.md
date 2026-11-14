# Cross-venue OKX regime replay — 12 November 2026

## Purpose and scope

This is an isolated research validation of the frozen 15m strategy families on OKX USDT-margined perpetual swaps. OKX is a cross-venue proxy, not BingX execution evidence. No production strategy, VST/REAL setting, risk setting, or live exit engine was changed.

The replay uses the five-symbol common universe BTC-USDT, ETH-USDT, SOL-USDT, HYPE-USDT, and INJ-USDT. The archive contains exactly 35,040 15m bars and 8,760 1h bars per symbol over the common 365-day window 2025-10-31 16:00 UTC through 2026-10-31 16:00 UTC (exclusive). Funding history contains 1,095 events per symbol except HYPE, which has 1,590; no funding events were excluded before the latest contiguous segment.

Signals use 15m bars and closed 1h context. The simulator is an OHLC execution proxy, not tick-level replay. Costs are charged per side:
- Base: 5 bps taker fee + 2 bps spread + 3 bps slippage.
- Stress: 7.5 bps fee + 3 bps spread + 4.5 bps slippage (1.5x each base component).

The chronological split is preserved by the research runner. The OOS interval has only three calendar months, and the number of trades is below the 100-trade gate for every family. No parameter tuning on OOS is permitted.

## Results

Net results at base costs:

| Frozen family | IS trades | IS PF | IS expectancy | Validation trades | Validation PF | Validation expectancy | OOS trades | OOS PF | OOS expectancy | OOS positive months |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Compression breakout | 182 | 0.5765 | -0.32581% | 34 | 0.9281 | -0.04087% | 56 | 0.4809 | -0.40216% | 0% |
| False-break reversal | 127 | 0.8432 | -0.08606% | 24 | 0.4908 | -0.35277% | 30 | 0.5079 | -0.33341% | 0% |
| Range reversion | 133 | 0.6118 | -0.26869% | 26 | 1.0324 | +0.01436% | 34 | 0.9475 | -0.02902% | 33.33% |
| Trend pullback | 149 | 0.5717 | -0.27396% | 24 | 0.6673 | -0.18243% | 44 | 0.8423 | -0.08241% | 33.33% |

At 1.5x costs, OOS results deteriorate:

| Frozen family | OOS trades | Stress OOS PF | Stress OOS expectancy |
|---|---:|---:|---:|
| Compression breakout | 56 | 0.4052 | -0.50216% |
| False-break reversal | 30 | 0.4202 | -0.43341% |
| Range reversion | 34 | 0.7901 | -0.12902% |
| Trend pullback | 44 | 0.6909 | -0.18241% |

The previously tested pullback continuation candidate also fails: 36 OOS trades, base PF 0.8509 / expectancy -0.08606%, stress PF 0.7113 / expectancy -0.18606%.

## Decision

No candidate passes the research gate. All families have negative OOS net expectancy; all OOS sample sizes are below 100; none reaches net PF 1.25; all fail the 1.5x-cost stress. Do not promote any family, do not combine them into a live router, and do not tune the OOS interval.

Range reversion is only the least-bad family in this specific proxy run. Its OOS PF 0.9475 is still below break-even, its sample is only 34 trades, and its 1.5x-cost PF falls to 0.7901. It is not a qualified candidate.

## Next research path

1. Finish the public Hyperliquid wallet archive analysis as cross-venue behaviour evidence, with explicit cohort/survivorship and data-field limitations.
2. Derive candidate flow/auction-transition features from historical wallet trade direction, size, taker aggression, and timing. Do not infer actual position inventory, entry/exit, or holding time from matched public trade prints alone.
3. Implement the feature as a separate research-only module and test it on OKX, then repeat the surviving frozen rule on BingX-native data when sufficient history is available.
4. Keep BingX and OKX results separate. Never represent OKX trades or wallet fills as BingX executions.

## Evidence

- Workflow: [OKX public history and regime replay run 43240965020](https://github.com/Gamadril34rus/astra_bot/actions/runs/43240965020)
- Artifact: `okx-public-history-and-replay` (artifact ID `13954574106`)
- Coverage correction independently verified in run `43240467525`; final 15m close is included in the 365-day coverage calculation.
- The earlier pullback-only 12-month replay is documented in PR #347; this report adds the independent four-family comparison.

All conclusions are research-only and do not establish future profitability.
