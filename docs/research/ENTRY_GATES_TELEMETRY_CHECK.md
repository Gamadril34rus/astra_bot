# Entry gates telemetry check (pre-slice)

**Default until owner live jsonl says otherwise: `FAIL_KEEP_SHADOW`.**

## (a) Merged on master? — YES

One-command style checks (master tree):

| Check | Result |
|-------|--------|
| `astra_bot/decision/entry_gates.py` present | **YES** |
| `EntryGateConfig.enabled` default | **False** (live off) |
| `EntryGateConfig.shadow_enabled` default | **True** |
| `astra_bot/ml/no_trade_observations.py` defines `_hypothetic_r` | **YES** |
| Outcomes write `hypothetic_r` | **YES** (`outcome_row["hypothetic_r"]`) |
| JSONL backfill copies `hypothetic_r` | **YES** |
| `trading_engine._record_entry_gate_rejection` passes entry/stop/take candidate | **YES** |
| `rejection_stage="entry_gate"` | **YES** |

Work B6 / entry_gates path is on master (module docstring references PR #78 part 3; live enable deferred to owner slice ~26–27.09).

## (b) Owner action

On the bot host, with live journal:

```bash
python scripts/research/entry_gate_shadow_report.py models/no_trade_observations.jsonl
```

Paste stdout into the slice notes.

## (c) If hypothetic_r is all None

Treat as **telemetry bug class** (field not written / enrichment not run / missing candidate geometry) — micro-fix before enable decision; enable **postponed**. Sample repo jsonl previously showed n_gate=10, hypothetic_r none=10 → FAIL_KEEP_SHADOW.

Enrichment requires later candles + `enrich`/`backfill_jsonl` path; shadow-only rejects still need candidate with nonzero entry/stop.

## (d) Enable path (pre-registered)

Only if live report: **n≥30** and **median(hypothetic_r)<0** → owner may set `entry_gates_enabled=True` at slice (path B). Otherwise keep shadow.

**No panic. Default remains FAIL_KEEP_SHADOW.**
