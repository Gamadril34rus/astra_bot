# SAR reproducible backtest package

**Status:** research only — **not** wired into ASTRA DEMO/live.

## Files in this directory (master)

| File | Role |
|------|------|
| `sar_backtest_engine.py` | Full Parabolic SAR + portfolio engine (run this) |
| `summary.json` | Snapshot metrics from 2026-11-18 run |
| `verification_sample.json` | Spot-checks for equity min/max / first trade |
| `README.md` | This file |

**CSV trades/equity are not stored in git** (size). Generate them:

```bash
cd docs/research/sar_repro
pip install requests pandas numpy
python sar_backtest_engine.py --start 5000 --out sar_repro_out
```

Produces: `trades_*.csv`, `equity_*.csv`, `trades_raw_signals.csv`, refreshed `summary.json`.

---

## Rules encoded in the engine

- SAR `0.02 / 0.02 / 0.2`, interval `1h`
- Symbols: BTC-USDT, ETH-USDT, SOL-USDT
- Entry: SAR side flip on closed bar, `min_prev_series = 0`
- Exit: touch SAR stop (`min(open,stop)` / `max(open,stop)` on that bar)
- Sizing: `notional = (risk * equity) / risk_dist`
- Skip if open risk sum + risk > `max_total_risk`
- Costs: taker **0.05%** + slip **0.1%** per side (bot paper model)
- **Funding = 0** (known gap)

---

## Snapshot 2026-11-18 (see summary.json)

| Window | |
|--------|--|
| BTC/ETH | 2025-11-08 → 2026-11-18 (~8992 bars) |
| SOL | 2025-12-20 → 2026-11-18 (~7993 bars) |

| Profile | Final @5k | Max DD | Trades | WR | PF |
|---------|------------|--------|--------|-----|-----|
| 1% / 3% | ~200,898 | −41.4% | 1975 | 41.6% | 1.70 |
| 1.5% / 6% | ~867,442 | −55.6% | 1975 | 41.6% | 1.70 |
| 2% / 8% | ~2,976,227 | −66.7% | 1975 | 41.6% | 1.70 |
| 3% / 12% | ~15,064,539 | −87.6% | 1975 | 41.6% | 1.70 |

**3% is not a “reasonable” profile** just because final is largest. −87% DD means the path almost wiped the peak; recovery is not guaranteed live.

---

## Known gaps (do not treat capital multiples as proven)

1. **~1 year only**, SOL starts later than BTC/ETH.  
2. **Funding omitted.**  
3. **Simplified stop fill** — no full intrabar path, no gap model beyond slip.  
4. **No walk-forward / OOS** — single continuous window.  
5. **Compounding + high risk** inflates finals; PF 1.70 is the more stable signal than “millions”.  
6. BingX klines ≠ guaranteed BingX live fills.

### Next research steps (not done here)

- [ ] Historical funding applied per position lifetime  
- [ ] Stricter stop model / gap stress  
- [ ] Chronological walk-forward; per-symbol isolation  
- [ ] Baseline: flat SAR without portfolio reinvestment; buy-and-hold control  
- [ ] Optionally commit generated CSVs if size policy allows  

**Do not enable in DEMO until owner signs off after formula-level review of `sar_backtest_engine.py`.**
