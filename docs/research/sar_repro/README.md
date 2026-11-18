# SAR research backtest package

**Engine:** `v4_mtm_batch_entries`  
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
python sar_backtest_engine.py --start 5000 --out sar_repro_out
```

## v4 highlights

- Stop: `sar[i-1]`; entry: next open  
- **MTM MaxDD** (realized + unrealized hourly)  
- Simultaneous same-hour entries: one equity base + risk cap  
- Exit timestamps: close_ts (or open on gap-through)  
- Strict BingX fetch (HTTP/code/retry 100410/min bars)  
- Warmup 50 bars; init_trend flag  
- Funding still **0**

## Snapshot direction

Default SAR 0.02/0.02/0.2 1h under bot costs still **PF_usd ~0.75** on ~1y (preliminary). See `summary.json`.

## Sequence

1. Re-audit v4  
2. Freeze candles + funding  
3. Unit tests risk/gap/long-short  
4. Walk-forward + limited grid + OOS only after pass  
