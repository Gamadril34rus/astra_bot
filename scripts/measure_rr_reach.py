"""D4: достижимость тейка 2.3R на существующих paper-результатах.

Читает models/paper_trades.jsonl (поля r_multiple / mfe_r заполнены
брокером) и печатает: распределение итогового R и MFE, долю сделок,
досидевших до 0.8/1.0/1.8/2.0/2.3R, симуляцию «фикс на 2.3R при касании».
Только чтение. Запуск:

    python scripts/measure_rr_reach.py [путь_к_jsonl]
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

THRESHOLDS = (0.8, 1.0, 1.8, 2.0, 2.3, 2.5)
DEFAULT_PATH = "models/paper_trades.jsonl"


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH)
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    closed = [r for r in rows if r.get("closed_at")]
    rs = [float(r["r_multiple"]) for r in closed if r.get("r_multiple") is not None]
    mfes = [float(r["mfe_r"]) for r in closed if r.get("mfe_r") is not None]
    n = len(rs)
    print(f"файл: {path}  закрытых сделок: {n}")
    print(f"итоговый R: mean={statistics.mean(rs):+.2f} median={statistics.median(rs):+.2f} "
          f"max={max(rs):+.2f} min={min(rs):+.2f}")
    print(f"MFE:        mean={statistics.mean(mfes):.2f} median={statistics.median(mfes):.2f} "
          f"max={max(mfes):.2f}")
    for thr in THRESHOLDS:
        k = sum(1 for m in mfes if m >= thr)
        print(f"  MFE >= {thr:.1f}R: {k}/{n} = {k / n * 100:.1f}%")
    for target in (2.0, 2.3):
        sim = [target if m >= target else r for m, r in zip(mfes, rs)]
        print(f"симуляция 'фикс на {target}R при касании, иначе как было': "
              f"sum={sum(sim):+.1f}R (факт {sum(rs):+.1f}R), "
              f"WR={sum(1 for x in sim if x > 0) / n * 100:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
