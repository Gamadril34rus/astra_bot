#!/usr/bin/env python3
"""Restore trading_engine from origin/master and set min_rr=2.0."""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "astra_bot" / "decision" / "trading_engine.py"


def main() -> None:
    raw = subprocess.check_output(
        ["git", "show", "origin/master:astra_bot/decision/trading_engine.py"],
        cwd=ROOT,
    )
    t = raw.decode("utf-8")
    # Prefer explicit 3.0 sprint line; also catch bare 3.0 if comment differs
    if "cfg.min_rr = 3.0" in t:
        t = t.replace(
            "cfg.min_rr = 3.0  # Sprint 2026-09-23",
            "cfg.min_rr = 2.0  # Owner 24.09: align with clamp_take_rr 2.0–2.3",
            1,
        )
        if "cfg.min_rr = 3.0" in t:
            t = t.replace("cfg.min_rr = 3.0", "cfg.min_rr = 2.0  # Owner 24.09", 1)
    elif "cfg.min_rr = 2.0" not in t and "cfg.min_rr = 0.7" in t:
        t = t.replace("cfg.min_rr = 0.7", "cfg.min_rr = 2.0  # Owner 24.09", 1)
    ast.parse(t)
    assert "class TradingEngine" in t
    assert "cfg.min_rr = 2.0" in t
    assert "cfg.min_rr = 3.0" not in t
    OUT.write_text(t, encoding="utf-8")
    print("OK lines", len(t.splitlines()), "->", OUT)


if __name__ == "__main__":
    main()
