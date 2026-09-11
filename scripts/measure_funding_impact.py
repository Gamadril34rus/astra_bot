"""B11: замер влияния фандинга на существующие paper-результаты.

Читает models/paper_trades.jsonl (источник правды по закрытым сделкам)
и печатает: суммарные PnL / комиссии / фандинг, долю фандинга, статистику
времени удержания против 8-часового интервала фандинга.

Только чтение; ничего не пишет и не решает — вывод идёт в отчёт
docs/FUNDING_BACKTEST_VS_PAPER.md. Запуск:

    python scripts/measure_funding_impact.py [путь_к_jsonl]
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

DEFAULT_PATH = "models/paper_trades.jsonl"
FUNDING_INTERVAL_H = 8.0


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH)
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    closed = [r for r in rows if r.get("closed_at")]
    open_rows = [r for r in rows if not r.get("closed_at")]

    net_pnl = sum(float(r.get("pnl") or 0) for r in closed)
    fees = sum(float(r.get("fees") or 0) for r in closed)
    funding = sum(float(r.get("funding") or 0) for r in closed)
    holds_h = [
        (r["closed_at"] - r["opened_at"]) / 3_600_000
        for r in closed
        if r.get("opened_at")
    ]

    print(f"файл: {path}")
    print(f"сделок: всего={len(rows)} закрыто={len(closed)} открыто={len(open_rows)}")
    print(f"net PnL (закрытые): {net_pnl:.2f}")
    print(f"комиссии: {fees:.2f}")
    print(f"фандинг (+ заплатили / - получили): {funding:.2f}")
    if closed:
        print(f"фандинг как % от |net PnL|: {abs(funding) / abs(net_pnl) * 100:.2f}%")
    if holds_h:
        print(
            "удержание, часов: "
            f"медиана={statistics.median(holds_h):.2f} "
            f"среднее={statistics.mean(holds_h):.2f} "
            f"макс={max(holds_h):.2f}"
        )
        under_interval = sum(1 for h in holds_h if h < FUNDING_INTERVAL_H)
        print(
            f"сделок короче интервала фандинга ({FUNDING_INTERVAL_H:.0f}ч): "
            f"{under_interval}/{len(holds_h)}"
        )
    print("модель live: broker.funding_payment — pro-rata по времени (broker.py)")
    print("модель бэктестера: фандинга НЕТ (grep 'funding' astra_bot/backtester/ -> 0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
