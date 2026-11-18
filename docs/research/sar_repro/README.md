# SAR reproducible backtest package

**Status:** research only — **not** in ASTRA DEMO/live.  
**Engine:** `v2_no_lookahead_next_open_pf_usd` (see `AUDIT_V2.md`).

## Files

| File | Role |
|------|------|
| `sar_backtest_engine.py` | **v2** engine (no stop look-ahead, next-open entry, PF in USDT) |
| `summary.json` | **v2** metrics (loss-making on tested window) |
| `AUDIT_V2.md` | Why prior PF 1.70 is invalid |
| `README.md` | This file |

```bash
cd docs/research/sar_repro
pip install requests pandas numpy
python sar_backtest_engine.py --start 5000 --out sar_repro_out
```

---

## Execution rules (v2)

- SAR `0.02 / 0.02 / 0.2`, `1h`, BTC/ETH/SOL
- **Signal:** trend flip on **bar close**
- **Entry:** **next bar open** (+ slip)
- **Stop on bar i:** `sar[i-1]` only (known at open); gap through stop → fill at open
- Sizing: `notional = (risk * equity) / risk_dist`
- Costs: fee 0.05% + slip 0.1% per side; **funding = 0**
- **PF:** `profit_factor_usd` from cash PnL (not raw %)

---

## v1 results — INVALID

Look-ahead stop + entry on signal close + PF on % → **PF 1.70 and large finals are not valid.**

## v2 snapshot (bot costs, start 5000, ~1y)

| Profile | Final | Return | Max DD | PF_usd |
|---------|-------|--------|--------|--------|
| 1% / 3% | ~111 | **−98%** | **−98.5%** | **~0.77** |
| 1.5% / 6% | ~12 | −99.8% | −99.9% | ~0.79 |
| 2% / 8% | ~1 | −100% | −100% | ~0.81 |
| 3% / 12% | ~0 | −100% | −100% | ~0.84 |

Trades ~2060, WR ~31%. **Edge is negative after correct simulation.**

---

## Still open

- Funding
- Frozen candle archive
- Walk-forward / OOS
- Do not tune SAR parameters until engine changes are re-audited

**DEMO not touched.**
