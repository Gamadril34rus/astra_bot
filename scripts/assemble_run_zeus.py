#!/usr/bin/env python3
"""Assemble scripts/run_paper_zeus.py from _run_zeus_b64_*.txt parts."""
from pathlib import Path
import base64
import sys
root = Path(__file__).resolve().parent
parts = sorted(root.glob("_run_zeus_b64_*.txt"))
if not parts:
    print("no parts", file=sys.stderr)
    sys.exit(1)
data = "".join(p.read_text().strip() for p in parts)
out = root / "run_paper_zeus.py"
out.write_bytes(base64.b64decode(data))
print("wrote", out, "size", out.stat().st_size)
assert b"stats_store=zeus_stats" in out.read_bytes()
