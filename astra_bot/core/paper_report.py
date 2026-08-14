"""
Агрегат реальных paper-сделок для утреннего отчёта.

Читает models/paper_trades.jsonl (закрытые сделки живого движка) и
models/paper_positions.json (открытые позиции), считает статистику за
сутки и за всё время. Утренний отчёт должен показывать именно эти
сделки, а не synthetic self-play уроки.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

TRADES_PATH = Path("models/paper_trades.jsonl")
POSITIONS_PATH = Path("models/paper_positions.json")

MSK = timezone(timedelta(hours=3))


def _load_trades() -> list[dict[str, Any]]:
    # Источник закрытых сделок: paper_trades.jsonl, а если его нет
    # (например, после ребейза/конфликта) — live_lessons.jsonl, куда
    # движок пишет каждую закрытую сделку.
    candidates = [
        Path("models/paper_trades.jsonl"),
        Path("models/live_lessons.jsonl"),
    ]
    path = next((c for c in candidates if c.exists()), candidates[0])
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _load_open_positions() -> list[dict[str, Any]]:
    if not Path("models/paper_positions.json").exists():
        return []
    try:
        data = json.loads(Path("models/paper_positions.json").read_text(encoding="utf-8"))
        return data.get("positions", [])
    except Exception:
        return []


def _day_bounds_msk(reference: datetime | None = None) -> tuple[int, int]:
    """Вернуть (start_ms, end_ms) последних завершённых суток по МСК."""
    now = reference or datetime.now(tz=MSK)
    # Отчёт за вчерашние сутки 00:00–24:00 МСК.
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_midnight = today_midnight - timedelta(days=1)
    return (
        int(yesterday_midnight.timestamp() * 1000),
        int(today_midnight.timestamp() * 1000),
    )


def paper_stats() -> dict[str, Any]:
    trades = _load_trades()
    positions = _load_open_positions()

    total = len(trades)
    wins = [t for t in trades if float(t.get("pnl", 0)) > 0]
    losses = [t for t in trades if float(t.get("pnl", 0)) < 0]
    total_pnl = sum(float(t.get("pnl", 0)) for t in trades)

    start_ms, end_ms = _day_bounds_msk()
    day_trades = [
        t for t in trades
        if start_ms <= int(t.get("closed_at", 0) or 0) < end_ms
    ]
    day_wins = [t for t in day_trades if float(t.get("pnl", 0)) > 0]
    day_losses = [t for t in day_trades if float(t.get("pnl", 0)) < 0]
    day_pnl = sum(float(t.get("pnl", 0)) for t in day_trades)

    by_symbol = Counter(t.get("symbol", "?") for t in trades)
    best_symbols = by_symbol.most_common(5)

    win_rate = (len(wins) / total * 100) if total else 0.0
    gross_profit = sum(float(t.get("pnl", 0)) for t in wins)
    gross_loss = abs(sum(float(t.get("pnl", 0)) for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (
        float("inf") if gross_profit > 0 else 0.0
    )

    return {
        "total_trades": total,
        "total_wins": len(wins),
        "total_losses": len(losses),
        "total_pnl": total_pnl,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "day_trades": len(day_trades),
        "day_wins": len(day_wins),
        "day_losses": len(day_losses),
        "day_pnl": day_pnl,
        "open_positions": len(positions),
        "top_symbols": best_symbols,
        "open": positions,
    }


def format_paper_section() -> str:
    """Блок с реальными сделками для утреннего отчёта."""
    s = paper_stats()
    if s["total_trades"] == 0 and s["open_positions"] == 0:
        return (
            "\n\n💹 *Реальные сделки (демо OKX)*\n"
            "  Пока нет закрытых сделок. Идёт набор позиций."
        )

    def sign(v: float) -> str:
        return f"{v:+.2f}"

    lines = [
        "\n\n💹 *Реальные сделки (демо OKX)*",
        f"  За сутки: {s['day_trades']} сделок "
        f"(✅ {s['day_wins']} / ❌ {s['day_losses']}), "
        f"PnL {sign(s['day_pnl'])} USDT",
        f"  Всего: {s['total_trades']} сделок, "
        f"win-rate {s['win_rate']:.0f}%, "
        f"PF {s['profit_factor']:.2f}",
        f"  Накопленный PnL: {sign(s['total_pnl'])} USDT",
        f"  Открыто позиций: {s['open_positions']}",
    ]
    if s["top_symbols"]:
        top = ", ".join(f"{sym}×{n}" for sym, n in s["top_symbols"])
        lines.append(f"  Активные монеты: {top}")
    return "\n".join(lines)
