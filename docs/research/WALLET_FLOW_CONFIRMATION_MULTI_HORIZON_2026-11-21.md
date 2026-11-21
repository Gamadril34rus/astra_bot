# Wallet cohort sell-flow rule: multi-horizon replay and position-intent audit

**Date:** 2026-11-21  
**Status:** exploratory research only; strategy not approved  
**Scope:** PR #425, separate from Zeus and all production trading controls

## 1. Rule actually tested

The tested signal is not a confirmed large-trader short build. The source field `flow_ratio` is:

`sum(wallet buy-side notional - wallet sell-side notional) / sum(wallet-side participant notional)`

It aggregates selected cohort wallet-side prints, including maker and taker fills. Negative values are labelled `sell_pressure`; they do not distinguish Open Short from Close Long, and they are not aggressor-only flow. The event rule selects 9h `flow_ratio` sell-pressure observations when BingX's prior closed 1h return is positive and prior closed 4h return is negative.

The selected wallet cohort is a 100-address sample from a public leaderboard snapshot, not a market-wide census. The test window is 2026-10-03 00:15 UTC through 2026-11-08 10:15 UTC. Candidate entries are greedily non-overlapping by at least 9h per market. Entry is the first BingX 30m open at or after the completed 15m signal close, with a maximum 15m delay. The same entries are evaluated at 30m, 1h, 2h, 4h and 9h. Exit is the close of the 30m bar ending at the chosen horizon.

Actual archived BingX funding settlements are included, with positive funding credited to shorts. The fixed base round-trip cost assumptions are 12 bps for BTC/ETH and 18 bps for SOL/HYPE/INJ; the stress case is 1.5x. These are assumptions, not a measured historical spread/impact model or the owner's exact fee tier.

## 2. Multi-horizon results

The candidate set contains 32 entries: BTC 7, ETH 8, SOL 9, HYPE 8, INJ 0. The matched price-only baseline uses the same 1h-up/4h-down regime, the same data window and a greedy 9h non-overlap rule. Candidate and baseline entry sets are held constant across all five exit horizons.

Equal-market net returns are arithmetic means across BTC, ETH, SOL and HYPE, the four markets with at least five candidate events.

| Hold | Candidate net mean | Matched baseline net mean | Difference | UTC-day cluster bootstrap 95% interval for difference |
|---|---:|---:|---:|---:|
| 30m | -0.052% | -0.093% | +0.041 pp | [-0.062, +0.150] pp |
| 1h | -0.015% | -0.085% | +0.070 pp | [-0.164, +0.312] pp |
| 2h | +0.036% | -0.046% | +0.081 pp | [-0.244, +0.415] pp |
| 4h | +0.072% | -0.037% | +0.109 pp | [-0.328, +0.601] pp |
| 9h | +0.374% | +0.061% | +0.312 pp | [-0.384, +1.043] pp |

All five equal-market intervals cross zero. The aggregate rule therefore has no statistically supported excess over the matched price-only regime at any tested holding horizon.

### Per-market net means and excess over matched baseline

Values are net percent; excess is candidate minus baseline in percentage points.

| Market | Hold | Candidate | Baseline | Excess |
|---|---:|---:|---:|---:|
| BTC | 30m | -0.105 | -0.086 | -0.020 |
| BTC | 1h | -0.119 | -0.071 | -0.049 |
| BTC | 2h | -0.243 | -0.064 | -0.180 |
| BTC | 4h | +0.024 | -0.134 | +0.157 |
| BTC | 9h | +0.033 | +0.074 | -0.041 |
| ETH | 30m | -0.046 | -0.101 | +0.055 |
| ETH | 1h | +0.012 | -0.146 | +0.157 |
| ETH | 2h | -0.239 | -0.101 | -0.138 |
| ETH | 4h | +0.050 | -0.086 | +0.136 |
| ETH | 9h | +0.263 | +0.109 | +0.154 |
| SOL | 30m | -0.093 | -0.141 | +0.048 |
| SOL | 1h | +0.067 | -0.131 | +0.198 |
| SOL | 2h | +0.194 | -0.068 | +0.262 |
| SOL | 4h | -0.116 | -0.083 | -0.033 |
| SOL | 9h | -0.192 | +0.037 | -0.229 |
| HYPE | 30m | +0.037 | -0.045 | +0.081 |
| HYPE | 1h | -0.020 | +0.007 | -0.027 |
| HYPE | 2h | +0.432 | +0.050 | +0.381 |
| HYPE | 4h | +0.328 | +0.154 | +0.174 |
| HYPE | 9h | +1.390 | +0.025 | +1.366 |

The only strong slice is HYPE at 9h: eight events, seven distinct UTC dates, mean net +1.390% versus +0.025% for the matched baseline. Its 1.5x cost-stress mean is +1.301%. A separate HYPE-only day-cluster bootstrap gives a candidate-minus-baseline 95% interval of approximately [+0.359, +2.436] pp. However, HYPE's 30m, 1h, 2h and 4h excess intervals cross zero. The HYPE 9h result is a small discovery-sample slice, not an untouched holdout; one late-period event cannot validate it prospectively.

## 3. Position-intent audit

A separate 24h public Hyperliquid Info API audit queried 20 wallets selected from the frozen archive manifest. Eleven had fills during 2026-11-19 10:47 to 2026-11-20 10:47 UTC. The audit returned 41,039 fills across all markets; one wallet hit the 10,000-fill cap and no pagination stalls were reported.

The HYPE sell-side composition in this current 24h subset was:
- Sell / Open Short: **$242,844**, only **7.56%** of classified sell notional.
- Sell / Close Long: **$2,970,066**, **92.44%** of classified sell notional.
- Buy / Open Long: **$1,251,262**, **79.45%** of classified buy notional.
- Buy / Close Short: **$323,666**, **20.55%** of classified buy notional.
- No HYPE liquidation fills were flagged in this audit.

BTC sell-side composition was also mixed: $12.76m Open Short versus $15.99m Close Long, so only 44.38% of classified sell notional was new short opening. ETH and SOL showed high Open Short shares in this small current subset, but the active-wallet counts were limited.

**Critical boundary:** this 24h audit is not a historical classification of the 37-day candidate events. It cannot prove that the eight historical HYPE signals were mostly Close Long. It does prove that negative wallet-side flow must not be described as new short inventory without position reconstruction. The historical archive's signed buy/sell prints do not identify Open Short versus Close Long.

## 4. Verdict

- The generic five-market sell-flow rule is **NOT PASSED**: equal-market excess confidence intervals cross zero at every horizon and per-market results conflict.
- HYPE 9h is the only interesting exploratory candidate, but eight events are insufficient for strategy approval and the source signal's position intent is unverified.
- The tested signal is accurately described as **cohort net sell-side flow**, not large-wallet short opening or aggressive taker selling.
- No PF/portfolio drawdown claim is made. The replay is fixed-time, has no stop/target model, and lacks historical spread/market-impact calibration.
- Do not merge, promote, or connect this rule to Zeus.

## 5. Next decisive research gate

Before any further threshold tuning, reconstruct historical per-wallet position episodes for the same frozen cohort using the 37-day trade archive and the dated 2026-10-19 position/account-value anchor. For every candidate event, measure how much cohort sell notional was Open Short, Close Long, liquidation or unresolved, and do the same for buy notional. Keep only fully reconstructable wallet-market intervals. If historical position intent cannot be reconstructed, retain the signal only as net sell-side flow and require a prospectively frozen untouched BingX validation period. No thresholds may be tuned on the existing 37-day discovery archive.
