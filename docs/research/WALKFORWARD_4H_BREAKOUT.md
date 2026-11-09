> **INVALIDATED — DO NOT USE THESE PERFORMANCE METRICS.** A follow-up integrity check found that the archived CSVs in `research/binance-data` do not match their named markets on the stated dates. The published OOS PF 1.238 / n=81 is therefore not verified. See [RESEARCH_BREAKOUT_RETEST_4H.md](RESEARCH_BREAKOUT_RETEST_4H.md) for the audit and promotion gate.

# Walk-Forward Research Results (BTC/ETH/SOL 4h, 2021–2026)

**Historical report retained for audit only.** The reported figures below are not validated and must not be treated as evidence of a tradable edge.

**Best robust config:** `break` (breakout + retest) | RR=1.5 | vol ≥ 1.3 | ATR × 1.5

| Period | n | PF | mean R | WR | net @0.5% | maxDD |
|--------|---|----|--------|-----|-----------|-------|
| IS 2021-2023 | 57 | 1.71 | +0.334 | 56.1% | +9.9% | 2.0% |
| OOS 2024-2026 | 81 | **1.238** | **+0.131** | 49.4% | +5.3% | 3.2% |
| Full 2021-2026 | 145 | 1.315 | +0.170 | 50.3% | +12.8% | 3.2% |

Data was labelled as Binance USDT-M style klines in branch `research/binance-data`, but the label is not supported by the observed price series. Costs were modelled at approximately 0.18% round-trip. The source data must be replaced and the test rerun before any of these metrics can be used.

## Other configs (same walk-forward)

| Config | OOS PF | OOS mean R | OOS n |
|--------|--------|------------|-------|
| break RR=2.0 vol≥1.3 | 1.17 | +0.11 | 80 |
| break RR=1.5 vol≥1.5 | 1.23 | +0.13 | 71 |
| ema RR=2.0 vol≥1.8 | 1.02 | +0.01 | 145 |
| ema RR=2.0 vol≥2.0 | 1.06 | +0.04 | 105 |

1h variants of the same ideas: PF 0.76–0.84, large losses (costs dominate).

## Recommended bot settings

- Primary timeframe for entries/filters: **4h** (1h secondary only)
- Prefer **breakout + retest** strategies (`range_breakout_retest`, `book_breakout`, `volatility_breakout`)
- Volume confirmation: **≥ 1.3 × SMA20** on signal bar (1.5+ stricter)
- Stop: **1.5 × ATR(14)**; Take: **1.5–2.0 R** (partial 30–40% at 1R optional)
- Risk per trade: **0.3–0.5%** until live/paper PF > 1.2 on ≥ 30 trades
- Disable or heavily down-weight pure 1h momentum / scalp / weak EMA-pullback without volume+structure

## Reproduce

```bash
# CSVs from research/binance-data branch in data/
python scripts/research_bt_walkforward.py
```

*Paper research only. Past performance ≠ future results.*
