#!/usr/bin/env python3
"""Research-only: summarize entry_gates shadow from no_trade_observations.jsonl.

Does NOT enable gates. Owner slice 26-27.09 pre-registered criterion:
  median(hypothetic_r of rejected) < 0 AND n >= 30  → candidate to enable.

Usage:
  python scripts/research/entry_gate_shadow_report.py models/no_trade_observations.jsonl
  python scripts/research/entry_gate_shadow_report.py path/to/file.jsonl --min-n 30
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return xs[int(k)]
    return xs[f] * (c - k) + xs[c] * (k - f)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("jsonl", type=Path, help="models/no_trade_observations.jsonl")
    ap.add_argument("--min-n", type=int, default=30, help="pre-registered n threshold")
    args = ap.parse_args()

    if not args.jsonl.exists():
        print(f"FILE_MISSING: {args.jsonl}", file=sys.stderr)
        return 2

    rows: list[dict] = []
    for line in args.jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    gate_rows = []
    for r in rows:
        stage = str(r.get("rejection_stage") or r.get("stage") or "")
        reason = str(r.get("reason_code") or r.get("reason") or "")
        gate = r.get("gate") or r.get("entry_gate")
        if stage == "entry_gate" or reason.startswith("ENTRY_GATE") or gate:
            gate_rows.append(r)

    print(f"total_lines={len(rows)} entry_gate_related={len(gate_rows)}")

    by_gate: Counter[str] = Counter()
    hyps: list[float] = []
    none_n = 0
    for r in gate_rows:
        g = (
            r.get("gate")
            or r.get("entry_gate")
            or str(r.get("reason_code") or "").replace("ENTRY_GATE_", "")
            or "UNKNOWN"
        )
        by_gate[str(g)] += 1
        hr = r.get("hypothetic_r")
        if hr is None and isinstance(r.get("outcome"), dict):
            hr = r["outcome"].get("hypothetic_r")
        if hr is None:
            none_n += 1
        else:
            try:
                hyps.append(float(hr))
            except (TypeError, ValueError):
                none_n += 1

    print("by_gate:")
    for g, n in by_gate.most_common():
        print(f"  {g}: {n}")

    print(
        f"hypothetic_r: n={len(hyps)} none={none_n} "
        f"none_share={none_n / max(1, len(gate_rows)):.3f}"
    )
    if hyps:
        med = _pct(hyps, 0.5)
        q25 = _pct(hyps, 0.25)
        q75 = _pct(hyps, 0.75)
        print(f"  median={med:.4f} q25={q25:.4f} q75={q75:.4f}")
        print(f"  share_le_-1R={sum(1 for x in hyps if x <= -1) / len(hyps):.3f}")
        criterion = med is not None and med < 0 and len(hyps) >= args.min_n
        print(
            f"pre_registered: median<0 and n>={args.min_n} → "
            f"{'PASS_CANDIDATE_ENABLE' if criterion else 'FAIL_KEEP_SHADOW'}"
        )
    else:
        print("pre_registered: FAIL_KEEP_SHADOW (no hypothetic_r yet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
