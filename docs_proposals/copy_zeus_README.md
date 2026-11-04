# COPY_ZEUS — read-only probe and archive (Stages 0–1)

## Safety boundary

This package is read-only. It exposes only GET requests to positions, allOrders and income.
There are no POST/DELETE methods and no order-placement/cancellation path. Credentials are
read from `BINGX_API_KEY` and `BINGX_API_SECRET`; never print the key, secret, signed URL,
signature, or request headers. Diagnostics may show only a masked identifier such as `***abcd`.

Set `COPYZEUS_ENV=vst` for `https://open-api-vst.bingx.com`; otherwise the environment
is `real` and uses `https://open-api.bingx.com`. Stage 0 workflow is manual
(`workflow_dispatch`) and produces only the sanitized `probe-report` artifact.

## Stage 0 probe

Run with the repository root on `PYTHONPATH`:

```bash
COPYZEUS_ENV=vst python scripts/copy_zeus_probe.py
```

The probe requests positions, allOrders and income with page limits no greater than five.
Income is paged backward for at most five pages over a 24-hour starting window. The report
contains response schema, counts, one sanitized sample per endpoint, observed income types,
counts, identifier fields actually present per type, and observed history depth. It does not
write raw API responses to the repository.

## Stage 1 archive

```python
import asyncio
from astra_bot.copy_zeus.archive import CopyZeusArchive

asyncio.run(CopyZeusArchive(env="vst").run_once())
```

Archive roots:
- `models/copy_zeus/vst/archive.jsonl`
- `models/copy_zeus/real/archive.jsonl`

Each line records `env`, `source`, `fetched_at`, the unmodified exchange event under
`event`, and an integrity marker. `seen.json` tracks deduplication keys from identifiers
actually present (falling back to a stable full-record digest); `coverage.json` records
observed intervals and explicit gaps. Income transfers, deposits and withdrawals remain
separate raw income events; unknown income types are preserved, never discarded. Archive
writes are append-only and idempotent on rerun. The archive directory is git-ignored and
must never be force-added or included in a workflow SAVE_LIST.

## What this data does and does not mean

- **allOrders is order history, not fill/execution history.** An order can have partial fills,
  cancellations, or multiple fills. The approved endpoint set does not guarantee fills.
  Any fills endpoint requires separate owner approval and is not implemented here.
- **positions is a point-in-time snapshot only.** A position opened and closed between
  snapshots will not appear. Absence from a snapshot does not mean no trade. Each snapshot
  is stored as an independent observation with its own `fetched_at`; it is never merged
  into open/close events and no position history is inferred.
- **income is a financial-event feed.** Only types actually returned by the API are known;
  unknown values are preserved.
- Stage 1 supports safe incremental collection and bounded backward pagination of available
  history. It does **not** promise continuous automatic collection. Continuous collection
  belongs to a separately approved Stage 2.
- The leader's original trade details and exact copy-trading delay are not observable from
  these owner-account endpoints and are not inferred.

## Tests

`pytest tests/copy_zeus -q` uses fixtures and temporary directories only; tests do not
make network requests or write into `models/copy_zeus`.
