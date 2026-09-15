# TSM45 panel — frozen spec on TRADING_UNIVERSE (32/35 pairs)

**Params (locked, no tuning):** lookback=270 bars (45d@4h), band=±2%, flip exit, 6×ATR stop, fee RT=0.30%, max hold 500 bars.

**Data:** Binance.US public klines 4h (Binance.com returned HTTP 451). Missing: TONUSDT, CROUSDT, XMRUSDT.

**Windows:** IS 2024-08-31→2025-08-31; OOS 2025-08-31→2026-08-31 (same as `strategy_tsm45_spec.md`).

## Aggregate

| Period | n | PF | mean net |
|--------|--:|---:|---------:|
| IS | 738 | 1.248 | +0.0126 |
| OOS | 826 | **0.853** | **−0.0061** |
| Full 2021–2026 | 3480 | 1.124 | +0.0062 |

OOS mean>0: **16/32** symbols (not majority).

## Per-year aggregate

| Year | n | PF | mean |
|------|--:|---:|-----:|
| 2021 | 315 | 1.46 | +0.034 |
| 2022 | 558 | 0.80 | −0.011 |
| 2023 | 550 | 1.55 | +0.024 |
| 2024 | 691 | 1.50 | +0.024 |
| 2025 | 697 | 0.85 | −0.008 |
| 2026 | 669 | 0.72 | −0.011 |

## Verdict

Panel OOS does **not** confirm the BTC-lab anecdote (n=18, PF 1.91). Sign is mixed; aggregate OOS negative. **No** 35-pair paper-shadow grant. Optional narrow shadow only on symbols with OOS mean>0 and adequate n, under standard probation.

## CSV

`reports/tsm45_panel_by_symbol.csv`
