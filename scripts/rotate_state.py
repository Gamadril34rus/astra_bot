#!/usr/bin/env python3
"""Прокрутка торговых state-файлов (TZ §29).

Запуск:

    python scripts/rotate_state.py [--root models]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astra_bot.core.state_rotation import rotate_all


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", default="models")
    args = p.parse_args()
    moved = rotate_all(Path(args.root))
    print(f"Rotation: {moved or 'ничего не требовалось'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
