#!/usr/bin/env python3
"""One-off, idempotent migration of Zeus and the frozen twin's NO_TRADE outcomes.

    python scripts/migrate_no_trade_outcomes.py --dry-run
    python scripts/migrate_no_trade_outcomes.py
    git add -f models/*no_trade_outcomes.jsonl models/archive/*no_trade_outcomes_*.json.gz
    git add -u models/*no_trade_outcomes.json

The complete original JSON bytes are snapshotted to models/archive before
removing legacy JSON. Rows are sorted by bar_time (milliseconds), computed_at,
then id for deterministic ties; only the LIVE_JSONL_LIMITS tail stays live.
Archives MUST be explicitly staged: runtime workflows do not commit them.
An existing different JSONL is never overwritten (it may contain new appends).
The twin is frozen; this migration prepares future reactivation, not trading.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astra_bot.core.state_rotation import LIVE_JSONL_LIMITS
from astra_bot.ml.no_trade_outcomes import legacy_outcomes, outcome_sort_key, outcomes_jsonl_path

SOURCES = ("models/zeus_no_trade_outcomes.json", "models/no_trade_outcomes.json")


def migrate_file(
    source: Path, *, dry_run: bool = False, stamp: str | None = None
) -> dict[str, Any]:
    """Snapshot, create a bounded JSONL tail atomically, then remove legacy JSON."""
    destination = outcomes_jsonl_path(source)
    report: dict[str, Any] = {
        "source": str(source),
        "destination": str(destination),
        "migrated": False,
        "skipped": "",
    }
    if not source.exists():
        report["skipped"] = "already_migrated" if destination.exists() else "missing"
        return report

    raw = source.read_bytes()
    rows = sorted(legacy_outcomes(json.loads(raw)).items(), key=outcome_sort_key)
    limit = LIVE_JSONL_LIMITS[destination.name]
    kept = rows[-limit:]
    content = "".join(
        json.dumps({"id": oid, **row}, ensure_ascii=False, separators=(",", ":")) + "\n"
        for oid, row in kept
    ).encode("utf-8")
    # A previous interrupted run may already have published exactly this tail.
    # Anything else could be live data: refuse to replace it.
    if destination.exists() and destination.read_bytes() != content:
        raise ValueError(f"{destination} already exists with different content; refusing to overwrite")

    stamp = stamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = source.parent / "archive" / f"{source.stem}_{stamp}.json.gz"
    report.update(
        archive=str(archive),
        source_sha256=hashlib.sha256(raw).hexdigest(),
        total_rows=len(rows),
        kept_rows=len(kept),
        archived_only_rows=len(rows) - len(kept),
        limit=limit,
        jsonl_bytes=len(content),
    )
    if dry_run:
        report["skipped"] = "dry_run"
        return report

    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        if gzip.decompress(archive.read_bytes()) != raw:
            raise ValueError(f"snapshot collision: {archive}")
    else:
        # Publish a complete snapshot atomically and exclusively. A crash while
        # compressing cannot leave a truncated final .gz; a concurrent migration
        # cannot overwrite an earlier full snapshot.
        fd, name = tempfile.mkstemp(prefix=f".{archive.name}.", suffix=".tmp", dir=archive.parent)
        tmp_archive = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream, gzip.GzipFile(
                filename="", fileobj=stream, mode="wb", mtime=0
            ) as compressed:
                compressed.write(raw)
            if gzip.decompress(tmp_archive.read_bytes()) != raw:
                raise ValueError(f"snapshot verification failed: {tmp_archive}")
            archive.hardlink_to(tmp_archive)
        finally:
            tmp_archive.unlink(missing_ok=True)
    if gzip.decompress(archive.read_bytes()) != raw:
        raise ValueError(f"snapshot verification failed: {archive}")

    if not destination.exists():
        tmp = destination.with_suffix(".jsonl.tmp")
        tmp.write_bytes(content)
        tmp.replace(destination)
    source.unlink()
    report["migrated"] = True
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help="repository root")
    parser.add_argument("--dry-run", action="store_true", help="report only; do not write or remove files")
    args = parser.parse_args()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    try:
        for relative in SOURCES:
            print(json.dumps(migrate_file(args.root / relative, dry_run=args.dry_run, stamp=stamp)))
    except (OSError, ValueError) as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
