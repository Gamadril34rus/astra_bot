"""Executable wrapper around ultimate_strategy.py with corrected config grid."""
from __future__ import annotations

import importlib.util
import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "research" / "ultimate_strategy.py"
spec = importlib.util.spec_from_file_location("ultimate_strategy", MOD_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load {MOD_PATH}")
u = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = u
spec.loader.exec_module(u)


def candidate_grid() -> list[u.Config]:
    out: list[u.Config] = []
    for lb, band, astop, trail in itertools.product((30, 45, 60, 90), (0.01, 0.02, 0.03), (3.0, 4.0, 5.0), (0.0, 2.0, 3.0)):
        out.append(u.Config("tsm", lb, band, astop, trail, 0, 20.0, 200, 0, 0.0, 0.0))
    for lb, astop, trail, adxv in itertools.product((20, 40, 60, 100), (2.5, 3.5, 5.0), (0.0, 2.0, 3.0), (0.0, 20.0, 25.0)):
        out.append(u.Config("breakout", lb, 0.0, astop, trail, 180, adxv, 0, 0, 0.0, 0.0))
    for astop, trail, adxv, emaf in itertools.product((2.0, 3.0, 4.0), (0.0, 2.0, 3.0), (0.0, 20.0, 25.0), (200, 400)):
        out.append(u.Config("ema_pullback", 30, 0.0, astop, trail, 120, adxv, emaf, 0, 0.0, 0.0))
    for lb, band, astop, trail, adxv in itertools.product((30, 45, 60), (0.01, 0.02, 0.03), (3.0, 5.0), (0.0, 2.0), (0.0, 20.0)):
        out.append(u.Config("vol_break", lb, band, astop, trail, 0, adxv, 0, 0, 0.0, 0.0))
    return out


u.candidate_grid = candidate_grid

if __name__ == "__main__":
    u.main()
