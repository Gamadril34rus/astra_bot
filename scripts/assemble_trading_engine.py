#!/usr/bin/env python3
"""Assemble trading_engine.py from base64 chunks under scripts/_te_chunks/."""
from pathlib import Path
import base64
root = Path(__file__).resolve().parents[1]
parts = sorted((root / "scripts" / "_te_chunks").glob("te_chunk_*.b64"))
assert parts, "no chunks"
data = base64.b64decode("".join(p.read_text().strip() for p in parts))
out = root / "astra_bot" / "decision" / "trading_engine.py"
out.write_bytes(data)
print("wrote", out, "bytes", len(data))
