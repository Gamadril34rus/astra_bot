# Recommended configuration from lesson + walk-forward research (2021–2026)

Source: branch `research/lesson-edge-4h-breakout`, data BTC/ETH/SOL 4h, costs 0.18% RT.

## Edge that survived OOS (2024–2026)

| Metric | IS 2021–2023 | OOS 2024–2026 | Full |
|--------|--------------|---------------|------|
| Strategy | breakout + retest | same | same |
| n | 57 | 81 | 145 |
| Profit Factor | 1.71 | **1.24** | 1.32 |
| mean R | +0.33 | **+0.13** | +0.17 |
| Win rate | 56% | 49% | 50% |
| net @ risk 0.5% | +9.9% | +5.3% | +12.8% |
| max DD | 2.0% | 3.2% | 3.2% |

Params (fixed before looking at OOS):
- Timeframe: **4h**
- Entry: breakout of 20-bar high/low → retest hold + close beyond level
- Filter: EMA20 > EMA50 (long) / opposite (short), volume ≥ 1.3× SMA20
- Stop: 1.5 × ATR(14)
- Take: 1.5 R (or 2.0 R — still positive OOS)
- Risk: 0.3–0.5% of equity per trade

## What failed / stays negative

- Pure EMA-pullback without strong volume: PF < 1 on full sample (or barely >1 only at vol≥2.0)
- All 1h variants: heavy loss (costs + noise)
- Higher RR (2.5+) reduces PF vs 1.5–2.0

## Concrete bot changes

1. **Raise weight / priority** of `range_breakout_retest`, `book_breakout`, `volatility_breakout` on 4h.
2. **Require** volume ratio ≥ 1.3 (preferably 1.5) on the signal bar for these strategies.
3. **Default take RR** for them: clamp to 1.5–2.0 (not 2.3+).
4. **Primary decision TF**: 4h for the breakout family.
5. **Risk per trade**: keep ≤ 0.5% (prefer 0.4%) until paper/live n≥30 and PF>1.2.
6. Kill or probation pure 1h momentum/scalp/weak pullback until they show OOS edge with same cost model.

## How to reproduce

```bash
# Place BTCUSDT_4h.csv ETHUSDT_4h.csv SOLUSDT_4h.csv in data/
python scripts/research_bt_walkforward.py
```

No parameter was optimized on the OOS window.
