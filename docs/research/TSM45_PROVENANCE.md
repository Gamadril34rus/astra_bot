# tsm45_ls OOS PF=1.91 — provenance

**Source of record (in-repo):** [`docs/strategy_tsm45_spec.md`](../strategy_tsm45_spec.md)

| Field | Value |
|-------|-------|
| Strategy | TSM 45d L/S (`TimeSeriesMomentumStrategy`) |
| TF | **4h** |
| IS window | 2024-08-31 → 2025-08-31 |
| IS | PF **2.32**, +5.6%, DD 2%, **n=23** |
| OOS window | 2025-08-31 → 2026-08-31 |
| OOS | PF **1.91**, +3.8%, DD 4%, **n=18** |
| Full 2021–2026 | PF 1.50, +17%, DD 8%, Sharpe 0.54, n=113 |
| Config | lookback_days=45, band=±2%, allow_short=True, adx_min=0, atr_stop_mult=6.0, **no take-profit**, exit on **regime flip** |
| Lab script | `scripts/strategy_lab.py` → `reports/strategy_lab/` |
| Seed | not stated as single bootstrap seed in the spec (lab walk-forward 36 windows, 83.3% PF≥1.10) |

## Decision rule (owner canon)

**n_OOS = 18 < 30 → anecdote.** Does **not** enter promotion / weight decisions alone. Spec itself says: live only after 3 months paper with OOS PF≥1.10.

## Why our RR=2 proxy failed TSM

Production TSM lets winners run until flip; forcing 2R take + 0.30% RT is a **different strategy**. Negative proxy PF does not falsify the flip-exit thesis — it falsifies “TSM with pipeline clamp_take_rr”.
