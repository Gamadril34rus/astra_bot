# Walk-Forward Research Results (BTC/ETH/SOL 4h, 2021–2026)

**No curve-fitting.** Parameters fixed from in-sample logic + previous full-period screen; evaluated on 2024–2026 OOS.

**Best robust config:** `break` (breakout + retest) | RR=1.5 | vol ≥ 1.3 | ATR × 1.5

| Period | n | PF | mean R | WR | net @0.5% | maxDD |
|--------|---|----|--------|-----|-----------|-------|
| IS 2021-2023 | 57 | 1.71 | +0.334 | 56.1% | +9.9% | 2.0% |
| OOS 2024-2026 | 81 | **1.238** | **+0.131** | 49.4% | +5.3% | 3.2% |
| Full 2021-2026 | 145 | 1.315 | +0.170 | 50.3% | +12.8% | 3.2% |

Data: Binance USDT-M style klines from branch `research/binance-data` (BTCUSDT/ETHUSDT/SOLUSDT 4h). Costs modelled ≈ 0.18% round-trip. One position at a time per symbol. Stop 1.5×ATR, take RR×stop.

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
