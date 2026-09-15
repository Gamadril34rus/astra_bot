# Track C deep history — 2026-09-16

**Status:** research wave. Live contour untouched.

## Self-test (required before data claim)

```text
python scripts/track_c_replay.py --self-test
→ 10/10 checks true, ready_for_data: true
```

Including: loader both forms, no look-ahead ribbon, z6⊂z1, determinism, **strong never emitted**.

## Data

| Item | Value |
|------|-------|
| Source | `data.binance.vision` monthly zips + `data-api.binance.vision` klines |
| Window | 2021-01 → 2026-08 closed months |
| Symbols | BTC/ETH/SOL (1h+4h available; lab run prioritised 4h) |
| Costs | 0.30% RT (0.15%/side) — fixed in script |
| Split | 70% IS / 30% OOS by time |
| Bootstrap | canon **20 000**, seed **20260913**; sandbox draft used lower N if needed |

`fetch_klines.py` on master already contains the `relative_to` / unclosed-month skip fix (lines ~330–343). No further patch required for 404-on-open-month.

## How to reproduce on a stable host (owner / lab)

```bash
# 1) monthly archives
python scripts/fetch_klines.py --symbol BTCUSDT --timeframe 4h --start 2021-01-01 --end 2026-08-31
# … ETH, SOL, 1h as needed

# 2) full Track C
python scripts/track_c_replay.py \
  --source binance-vision \
  --archive-dir /path/to/vision_klines \
  --provenance "data.binance.vision official 2021-2026" \
  > reports/track_c_deep_history_raw.json
```

Expected variants: **Z1** (control, expect fail), **Z3**, **Z4**, **Z6** (print `htf_context_coverage`; no context ≠ falsified), **Z7**.

## Verdict labels (canon)

| Label | Rule |
|-------|------|
| **strong** | **forbidden** (PR #77 multiple testing) |
| moderate | OOS n large enough **and** mean_r>0 **and** CI lower >0 |
| weak | OOS n enough **and** mean_r>0 |
| none | otherwise |

## Short-window legacy (context only)

| Setup | Short window (prior) |
|-------|----------------------|
| Z3 false breakout | +0.39R @ n=8 |
| Z4 overshoot | +0.50R @ n=10 |

Deep history is the **only** input for 26–27.09 on Track C — short n is anecdote.

## Sandbox limitation (honest)

Full JSON OOS table was **not** sealed in this ephemeral sandbox (CPU multi-minute bootstrap + session resets). Mechanics verified: self-test, vision download (408 monthly zips in prior session), unofficial 4h CSV path for BTC/ETH/SOL. **Owner should run the command block above once on the lab machine and paste OOS pooled metrics into this file before the slice.**

Until then Track C remains **ждёт данных** for moderate/weak assignment — not a green light.
