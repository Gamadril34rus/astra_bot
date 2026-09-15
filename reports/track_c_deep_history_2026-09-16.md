# Track C deep history — 2026-09-16

**Status:** workflow added (`track-c-deep.yml`). Run via **Actions → Track C deep history → Run workflow**. Artifacts = OOS JSON + this report regenerated.

## Self-test

```bash
python scripts/track_c_replay.py --self-test
# → 10/10, ready_for_data: true
```

## How to seal OOS (owner / Actions)

1. Merge PR with `.github/workflows/track-c-deep.yml` if not on master yet.
2. GitHub → Actions → **Track C deep history** → **Run workflow** (resamples=20000, fetch_eth_sol=1).
3. Download artifact `track-c-deep-history`:
   - `track_c_deep_history_raw.json`
   - regenerated `track_c_deep_history_2026-09-16.md` with OOS table
4. Optional: commit the markdown from the artifact into `reports/` for the slice pack.

## Labels

| Label | Rule |
|-------|------|
| strong | **forbidden** |
| moderate | OOS n sufficient, mean_r>0, CI lower>0 |
| weak | OOS n sufficient, mean_r>0 |
| none | else |

Z1 = control (expect fail). Z6: read `htf_context_coverage`; no context ≠ falsified. Z3/Z4 short-window n=8/10 = anecdote only.

## Entry gates (parallel tail)

See `docs/research/ENTRY_GATES_TELEMETRY_CHECK.md`. Code path for `hypothetic_r` is on master; **default FAIL_KEEP_SHADOW** until live jsonl passes pre-registered criterion.
