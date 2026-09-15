# OOS R distribution (breakout-retest 4h, n=81, 2024–2026)

## Fact vs implied sd

From bootstrap P(meanR ≤ 0) = 0.171 under research costs, a normal approximation with
SE = sd/√n and P ≈ Φ(−mean/SE) implies SE ≈ mean/0.95 ≈ 0.138 → **sd ≈ 0.138 × √81 ≈ 1.24**.

**Empirical sd = 1.249** (research 0.18% RT) — matches the implication.
The earlier guess “sd≈0.8” was low; live paper sd 0.5–0.6 is a **different exit process**.

## Why research sd ≫ live paper sd

Research path is nearly **bimodal** (full stop or full take, no partials):

| Region | Share | Typical R |
|--------|------:|----------:|
| Near full stop (R < −0.5) | 51% | ≈ −1.08 |
| Mid (−0.5 … +1.0) | **1%** | — |
| Near full take (R > 1.0) | 48% | ≈ +1.38 |

Live paper with 30/70 scale-out, BE after first clip, mae_cut, time stops fills the middle → lower sd (0.5–0.6) is expected.

## Quantiles (research 0.18% RT)

| q | R |
|--:|--:|
| 1% | −1.18 |
| 5% | −1.15 |
| 25% | −1.08 |
| 50% | −1.03 |
| 75% | +1.42 |
| 95% | +1.46 |

Wins 40 / losses 41 · mean_win +1.38 · mean_loss −1.09 · mean +0.131 · **sd 1.25** · SE(mean) 0.139

## Same OOS under BingX 0.30% RT

mean +0.072 · **sd 1.25** · SE 0.139 · normal P(mean≤0)≈0.30
Quantiles shift slightly more negative (costs hit both sides).

**Takeaway for risk sizing:** do not use live sd 0.5–0.6 to size a full-stop/full-take 4h breakout system; use sd≈1.2 or model the actual exit mixture.
