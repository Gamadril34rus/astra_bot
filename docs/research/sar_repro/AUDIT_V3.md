# SAR research engine audit v3 (2026-11-18)

**Status:** research engine revised after independent audit. **Needs re-audit.**  
**DEMO / live ASTRA:** not touched.  
**Param search:** not started (premature until engine trusted).

## Audit findings accepted (v2 → v3)

| ID | Issue | v3 change |
|----|-------|-----------|
| P1 | MaxDD on insertion-order curve | Equity curve rebuilt from closed trades sorted by `exit_ts` before MaxDD |
| P1 | Open trades dropped at series end | `force_close_end` at last **completed** bar close; last bar dropped as incomplete |
| P1 | Risk ignores costs | `notional = risk*equity / (risk_dist + cost_buffer)` with `cost_buffer = 2*(fee+slip)` |
| P1 | Fees as `2*TAKER` on one base | Separate entry fee on entry fill, exit fee on exit fill |
| — | Gap continuity | `check_hourly_gaps`; snapshot had 0 gaps >1.5h |
| — | SAR init always long | `init_trend` CLI flag (default +1); sensitivity still not fully explored |
| — | Funding | still **0** |

## v3 snapshot (not a claim of edge)

Window ~2025-11-08 → 2026-11-18 1h, BTC+ETH+SOL, bars 8991 each, gaps 0.  
Trades 2063 (incl. 3 eod force-close). Start 5000. Bot paper costs.

| Profile | Final | Return | MaxDD | PF_usd |
|---------|-------|--------|-------|--------|
| 1% / 3% | ~403 | −92% | −94% | **~0.76** |
| 1.5% / 6% | ~97 | −98% | −99% | ~0.78 |
| 2% / 8% | ~21 | −99.6% | −99.7% | ~0.79 |

**Conclusion unchanged in direction:** after removing look-ahead and fixing portfolio accounting, default SAR 0.02/0.02/0.2 1h remains **loss-making** on this window under bot costs. Numbers still provisional until re-audit of v3 formulas.

## Explicit non-goals right now

- No SAR parameter grid / optimization  
- No DEMO enablement  
- No “millions” narratives  

## Required sequence (agreed)

1. Independent re-audit of v3 (SAR math, event order, risk, fees, concurrent positions).  
2. Freeze candles + historical funding.  
3. Formula tests: gaps, simultaneous positions, long/short split.  
4. Only then walk-forward + limited grid + OOS.  
5. Report per-symbol and long/short separately — not one blended PF.

## Files

- `sar_backtest_engine.py` — target version string `v3_audit_fixes`  
- `summary.json` — v3 snapshot  
- `AUDIT_V2.md` — prior look-ahead invalidation  
