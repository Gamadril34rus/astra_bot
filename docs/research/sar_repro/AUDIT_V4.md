# SAR research engine audit v4 (2026-11-18)

**Status after v3 re-audit:** Needs revision → **v4 implements the listed P1s**.  
**v4 still Needs re-audit** before any parameter search.  
**DEMO / live:** not touched.

## v3 findings → v4 changes

| ID | Issue | v4 |
|----|-------|----|
| P1 | MaxDD realized-only | Full **MTM** equity each hour: realized + unrealized on open positions; report both `max_dd_pct` (MTM) and `max_dd_realized_only_pct` |
| P1 | force-close / stop time = bar open_ts | Exit event uses **bar close_ts** (`open + 1h`) for intra-bar stop and EOD; gap-through at open uses open_ts |
| P1 | sequential same-timestamp sizing | Entries with same `entry_ts` share **one equity base**, joint risk cap, deterministic symbol order |
| P1 | silent short history | `fetch_klines` checks HTTP 200, BingX `code`, retries `100410`, `min_bars` |
| P2 | SAR init | `--init-trend` + **warmup 50 bars**; sensitivity +1/−1 logged in summary |
| P1 | cost_buffer guarantee | Buffer kept; still **not** a hard gap guarantee — document only |

## v4 snapshot (preliminary, start 5000, bot costs)

~1y 1h BTC+ETH+SOL, 8991 bars, 0 hourly gaps, warmup 50, trades 2048.

| Profile | Final (MTM) | DD MTM | DD realized | PF_usd |
|---------|-------------|--------|-------------|--------|
| 1% / 3% | ~387 (−92%) | ~−94% | ~−94% | **~0.75** |
| 1.5% / 6% | ~91 | ~−99% | ~−99% | ~0.76 |
| 2% / 8% | ~19 | ~−100% | ~−100% | ~0.77 |

`init_trend` +1 vs −1: **same** n/PF/final on this window (warmup dominates).

## Still open

- Funding = 0  
- Frozen candle archive  
- Cost buffer ≠ guaranteed risk on large gaps  
- Unit tests for long/short stop PnL in USDT  
- Walk-forward / OOS  

## Rule

**No SAR parameter optimization until v4 passes independent re-audit.**  
Negative PF is a warning, not a final verdict on SAR as a family.
