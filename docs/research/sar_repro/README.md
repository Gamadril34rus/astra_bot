# SAR research backtest package

**Engine:** `v4_mtm_batch_entries_interval_aware`  
**Status:** implements v3 audit P1s → **Needs re-audit**  
**DEMO / live:** not connected  
**Param search:** blocked

## Files

| File | Role |
|------|------|
| `sar_backtest_engine.py` | Research engine (check `engine_version` in file) |
| `summary.json` | Latest snapshot |
| `AUDIT_V2.md` … `AUDIT_V4.md` | Audit trail |

```bash
python sar_backtest_engine.py --start 5000 --interval 15m --limit-total 36000 --out sar_repro_15m_out
```

## v4 highlights

- Stop: `sar[i-1]`; entry: next open
- Interval-aware candle timestamps and gap checks (`15m` supported); video baseline is 15m  
- **MTM MaxDD** (realized + unrealized at each candle close)  
- Simultaneous same-candle entries: one equity base + risk cap  
- Exit timestamps: close_ts (or open on gap-through)  
- Strict BingX fetch (HTTP/code/retry 100410/min bars)  
- Warmup 50 bars; init_trend flag  
- Funding still **0**

## Video reproduction baseline (2026-11-18)

The supplied video explicitly shows TradingView XAUUSD on **15m** with Parabolic SAR `0.02 / 0.02 / 0.2`. Its on-screen rules are: wait for the candle to close with SAR dots below price to buy / above price to sell; place the initial stop at the first SAR point; move the stop to the next point each completed candle; exit when price touches the point; no take-profit. The video does not specify position sizing, leverage, costs, or a statistical sample. XAUUSD is an illustration only; this project tests BingX USDT-M perpetuals.

Run a 15m crypto analogue with approximately one year of bars per symbol:

```bash
python sar_backtest_engine.py --start 5000 --interval 15m --limit-total 36000 --out sar_repro_15m_out
```

The existing `summary.json` is still the earlier 1h snapshot and is **not** a 15m result. Funding remains zero and must be added before profitability conclusions. Tests: `pytest -q docs/research/sar_repro/test_sar_backtest_engine.py`.

## Snapshot direction

Default SAR 0.02/0.02/0.2 1h under bot costs still **PF_usd ~0.75** on ~1y (preliminary). See `summary.json`.

## Sequence

1. Re-audit v4  
2. Freeze candles + funding  
3. Unit tests risk/gap/long-short  
4. Walk-forward + limited grid + OOS only after pass

## 2026-11-21 BingX 15m history availability

A one-year request for 36,000 15m candles returned only 9,600 BTC-USDT bars from the public BingX endpoint. The reproducible baseline therefore uses the observed 9,600-bar window (about 100 days), not a one-year claim. This is a coverage limitation, not a profitability result. The workflow freezes the raw candle CSVs and SHA-256 hashes so the shorter sample can be audited. A positive result on this window would still require an independent longer archive and funding integration before any strategy verdict.
