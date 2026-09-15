# A/B proxy sweep on 4h OOS (2024–2026) — no parameter tuning

**Cost model:** BingX-like **0.30% RT**. Stop 1.5×ATR, take **2.0R** (pipeline-like clamp). BTC+ETH+SOL. Bootstrap 5k, seed=42.

**Important:** These are **proxies** of Claude group A/B ideas, **not** bit-identical production detectors. Production `engine.py` + flip exits (TSM) / pattern geometry still required for promotion decisions.

## Results

| Group | Proxy | n | PF | mean R | PF 95% CI | P(PF≤1) | Verdict |
|-------|-------|--:|---:|-------:|-----------|--------:|---------|
| A | trend_following | 1035 | 0.91 | −0.06 | [0.79, 1.03] | 0.93 | **skip** |
| A | rectangle | 335 | 0.84 | −0.11 | [0.67, 1.06] | 0.93 | **skip** |
| A | flag | 0 | — | — | — | — | skip_n (no fires) |
| A | cup | 236 | 0.89 | −0.08 | [0.66, 1.17] | 0.81 | **skip** |
| B | zscore_mean_reversion | 862 | 0.80 | −0.14 | [0.69, 0.92] | 1.00 | **skip** |
| B | liquidity_sweep | 701 | 0.79 | −0.14 | [0.67, 0.93] | 1.00 | **skip** |
| B | expanding_triangle | 465 | 0.73 | −0.19 | [0.59, 0.89] | 1.00 | **skip** |
| TSM | mom 90-bar proxy | 1392 | 0.87 | −0.09 | [0.77, 0.97] | 1.00 | **skip** (unfair vs flip-exit) |
| TSM | mom 45d/270-bar proxy | 1412 | 0.86 | −0.09 | [0.77, 0.97] | 0.99 | **skip** (unfair vs flip-exit) |

## Interpretation vs Claude live MFE/MAE

- **Group B** remains **no directional edge** on 4h under fixed RR exits — consistent with live MAE>MFE.
- **Group A** also fails under **0.30% RT + fixed 2R take** — consistent with Claude’s “MFE exists but fees/exit kill”. Fixed full-take research path is harsher than partial 30/70; still no free lunch.
- **TSM proxy is invalid for the production thesis**: `docs/strategy_tsm45_spec.md` uses **flip exit, no TP, 6×ATR catastrophe stop**. Forcing RR=2.0 destroys the edge definition. See tsm45 provenance note.

## Registry truth (code)

`astra_bot/decision/strategy_registry.py` currently lists **14** core keys (book_breakout, academy_hybrid_mtf, momentum, mean_reversion, pullback, high_winrate, selective, ts_momentum, ts_momentum_adx, multicurrency_mtf, livermore_pivot, soros_regime, druckenmiller_driver, tudor_risk) + **16** pattern strategy classes. Full production sweep of every detector through `engine.py` remains the acceptance test for a later code PR — not completed here without look-ahead-safe adapters.

## Verdict for 26–27.09 slice

Do **not** onboard Group B. Group A stays **shadow-only** if at all. Prefer **new 4h breakout keys** (PR #88) + honest TSM paper path per `strategy_tsm45_spec.md`, not proxy RR=2 tables.
