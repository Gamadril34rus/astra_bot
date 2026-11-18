# SAR research backtest package

**Engine:** `v3_audit_fixes`  
**Status:** P1 fixes applied after independent audit → **Needs re-audit**  
**DEMO / live:** not connected  
**Param search:** blocked until engine trusted

## Files

| File | Role |
|------|------|
| `sar_backtest_engine.py` | Research engine (see version string inside) |
| `summary.json` | Latest snapshot |
| `AUDIT_V2.md` | Look-ahead invalidation |
| `AUDIT_V3.md` | P1 curve / open trades / risk / fees |
| `README.md` | This file |

```bash
python sar_backtest_engine.py --start 5000 --out sar_repro_out
```

## v3 execution (summary)

- Signal: SAR flip on **close**; entry: **next open**
- Stop on bar i: **`sar[i-1]` only**
- EOD: force-close open positions at last **completed** bar; last bar dropped
- Fees: entry and exit notional separately
- Risk: `notional = risk*equity / (risk_dist + cost_buffer)`
- MaxDD: from equity path sorted by exit time
- Funding: **still 0**

## Snapshot direction (not proof)

Default SAR 0.02/0.02/0.2 1h BTC+ETH+SOL under bot costs remains **PF_usd < 1** on ~1y window after fixes. See `summary.json` / `AUDIT_V3.md`.

## Sequence (do not skip)

1. Re-audit v3 formulas  
2. Freeze candles + funding  
3. Gap / concurrent / long-short tests  
4. Walk-forward + limited grid + OOS  
5. Never rank parameters on untrusted simulator metrics  
