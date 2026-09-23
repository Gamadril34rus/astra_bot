#!/usr/bin/env python3
"""One-shot ruff fixes for sprint branch."""
from pathlib import Path

te = Path("astra_bot/decision/trading_engine.py")
t = te.read_text(encoding="utf-8")
old = """        from datetime import datetime, timezone
        _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")"""
new = """        _today = datetime.now(UTC).strftime("%Y-%m-%d")"""
if old in t:
    te.write_text(t.replace(old, new, 1), encoding="utf-8")
    print("engine: UTC fixed")
elif "datetime.now(UTC).strftime" in t:
    print("engine: already fixed")
else:
    raise SystemExit("engine anchor not found")
