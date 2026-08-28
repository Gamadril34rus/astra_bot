#!/usr/bin/env python3
"""CI-гейт: торговые state-файлы не растут бесконечно (TZ §29).

Выход 1, если какой-то живой JSONL превышает лимит (rotation cap +
сессийный запас). Запуск:

    python scripts/check_state_size.py [--root models]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astra_bot.core.state_rotation import size_gate


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", default="models")
    args = p.parse_args()
    violations = size_gate(Path(args.root))
    if violations:
        for v in violations:
            print(f"STATE-SIZE VIOLATION: {v}", file=sys.stderr)
        return 1
    print("State size OK: все живые JSONL в пределах лимитов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
