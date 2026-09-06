"""
Weekly Report — Block 8: еженедельный отчёт с анализом стратегий, режимов, PnL.

Собирает:
- Статистику по стратегиям (win rate, PF, avg R)
- Статистику по режимам рынка
- PnL по дням недели и часам
- Лучшие/худшие сделки
- Рекомендации по адаптации
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)

try:
    from astra_bot.data.state_manager import get_state_manager
    from astra_bot.learning.strategy_stats import StrategyStatsStore
    from astra_bot.learning.weights import load_weights
except Exception:
    get_state_manager = None
    StrategyStatsStore = None
    load_weights = None


def _parse_trades() -> list[dict[str, Any]]:
    """Parse trades from JSONL and DB."""
    trades: list[dict[str, Any]] = []
    seen: set[str] = set()

    # JSONL
    jpath = ROOT / "models" / "paper_trades.jsonl"
    if jpath.exists():
        for line in jpath.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                tid = str(obj.get("id", ""))
                if tid and tid in seen:
                    continue
                if tid:
                    seen.add(tid)
                trades.append(obj)
            except Exception:
                continue

    # DB
    db_path = ROOT / "data" / "trades.db"
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("SELECT id, symbol, direction, entry_price, exit_price, quantity, pnl, exit_reason, strategy, regime, timeframe, opened_at, closed_at FROM trades")
            for row in cur.fetchall():
                tid = row[0]
                if tid in seen:
                    continue
                seen.add(tid)
                trades.append(
                    {
                        "id": tid,
                        "symbol": row[1],
                        "direction": row[2],
                        "entry_price": row[3],
                        "exit_price": row[4],
                        "quantity": row[5],
                        "pnl": row[6],
                        "exit_reason": row[7],
                        "strategy": row[8],
                        "regime": row[9],
                        "timeframe": row[10],
                        "opened_at": row[11],
                        "closed_at": row[12],
                    }
                )
            conn.close()
        except Exception as e:
            logger.warning("DB parse failed: %s", e)

    return trades


def _weekly_stats(trades: list[dict[str, Any]], days: int = 7) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    cutoff_ms = int(cutoff.timestamp() * 1000)

    recent = []
    for t in trades:
        closed = t.get("closed_at")
        if closed is None:
            continue
        try:
            ts = int(closed)
            if ts < 10000000000:
                ts = ts * 1000
            if ts >= cutoff_ms:
                recent.append(t)
        except Exception:
            continue

    total_pnl = sum(float(t.get("pnl") or 0) for t in recent)
    wins = sum(1 for t in recent if float(t.get("pnl") or 0) > 0)
    losses = sum(1 for t in recent if float(t.get("pnl") or 0) <= 0)
    win_rate = wins / len(recent) * 100 if recent else 0

    # By strategy
    by_strategy: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "pnl": 0.0, "wins": 0})
    for t in recent:
        strat = t.get("strategy") or "unknown"
        by_strategy[strat]["count"] += 1
        by_strategy[strat]["pnl"] += float(t.get("pnl") or 0)
        if float(t.get("pnl") or 0) > 0:
            by_strategy[strat]["wins"] += 1

    # By regime
    by_regime: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "pnl": 0.0})
    for t in recent:
        regime = t.get("regime") or "UNKNOWN"
        by_regime[regime]["count"] += 1
        by_regime[regime]["pnl"] += float(t.get("pnl") or 0)

    # By hour
    by_hour: dict[int, dict[str, Any]] = defaultdict(lambda: {"count": 0, "pnl": 0.0})
    for t in recent:
        try:
            ts = int(t.get("closed_at") or 0)
            if ts < 10000000000:
                ts = ts * 1000
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            by_hour[dt.hour]["count"] += 1
            by_hour[dt.hour]["pnl"] += float(t.get("pnl") or 0)
        except Exception:
            continue

    best = max(recent, key=lambda x: float(x.get("pnl") or 0), default=None)
    worst = min(recent, key=lambda x: float(x.get("pnl") or 0), default=None)

    return {
        "total": len(recent),
        "pnl": total_pnl,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "by_strategy": dict(by_strategy),
        "by_regime": dict(by_regime),
        "by_hour": dict(by_hour),
        "best": best,
        "worst": worst,
    }


def _load_weights_info() -> dict[str, Any]:
    try:
        if load_weights:
            return load_weights()
    except Exception:
        pass
    wpath = ROOT / "data" / "weights.json"
    if wpath.exists():
        try:
            return json.loads(wpath.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def build_weekly_report() -> str:
    trades = _parse_trades()
    stats7 = _weekly_stats(trades, days=7)
    stats30 = _weekly_stats(trades, days=30)
    weights = _load_weights_info()

    lines = []
    lines.append(f"📈 ASTRA BOT — Weekly Report {datetime.now().strftime('%d.%m.%Y')}")
    lines.append("")
    lines.append(f"📊 За 7 дней: {stats7['total']} сделок, PnL {stats7['pnl']:.2f} USDT, WR {stats7['win_rate']:.1f}% ({stats7['wins']}W/{stats7['losses']}L)")
    lines.append(f"📊 За 30 дней: {stats30['total']} сделок, PnL {stats30['pnl']:.2f} USDT, WR {stats30['win_rate']:.1f}%")

    if stats7["by_strategy"]:
        lines.append("")
        lines.append("🎯 По стратегиям (7д):")
        for strat, data in sorted(stats7["by_strategy"].items(), key=lambda x: x[1]["pnl"], reverse=True):
            wr = data["wins"] / data["count"] * 100 if data["count"] else 0
            lines.append(f"  {strat}: {data['count']} сделок, PnL {data['pnl']:.2f}, WR {wr:.0f}%")

    if stats7["by_regime"]:
        lines.append("")
        lines.append("🌊 По режимам (7д):")
        for regime, data in sorted(stats7["by_regime"].items(), key=lambda x: x[1]["pnl"], reverse=True):
            lines.append(f"  {regime}: {data['count']} сделок, PnL {data['pnl']:.2f}")

    if stats7["by_hour"]:
        best_hour = max(stats7["by_hour"].items(), key=lambda x: x[1]["pnl"], default=None)
        worst_hour = min(stats7["by_hour"].items(), key=lambda x: x[1]["pnl"], default=None)
        if best_hour:
            lines.append("")
            lines.append(f"⏰ Лучший час: {best_hour[0]}:00 UTC — {best_hour[1]['count']} сделок, PnL {best_hour[1]['pnl']:.2f}")
        if worst_hour:
            lines.append(f"⏰ Худший час: {worst_hour[0]}:00 UTC — {worst_hour[1]['count']} сделок, PnL {worst_hour[1]['pnl']:.2f}")

    if stats7["best"]:
        b = stats7["best"]
        lines.append("")
        lines.append(f"🏆 Лучшая сделка: {b.get('symbol')} {b.get('direction')} PnL {float(b.get('pnl') or 0):.2f} ({b.get('strategy')})")

    if stats7["worst"]:
        w = stats7["worst"]
        lines.append(f"💀 Худшая сделка: {w.get('symbol')} {w.get('direction')} PnL {float(w.get('pnl') or 0):.2f} ({w.get('strategy')})")

    if weights:
        sw = weights.get("strategy_weights") or {}
        if sw:
            lines.append("")
            lines.append("⚖️ Веса стратегий:")
            for k, v in sorted(sw.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"  {k}: {v:.2f}")

    # Recommendations
    lines.append("")
    lines.append("💡 Рекомендации:")
    # Find losing strategies
    for strat, data in stats7["by_strategy"].items():
        if data["count"] >= 5 and data["pnl"] < 0:
            lines.append(f"  • {strat} в минусе 7д — снизить вес или отключить")
    # Find best regime
    if stats7["by_regime"]:
        best_regime = max(stats7["by_regime"].items(), key=lambda x: x[1]["pnl"])
        if best_regime[1]["pnl"] > 0:
            lines.append(f"  • Лучший режим {best_regime[0]} — фокус на нём")

    if stats7["total"] < 5:
        lines.append("  • Мало сделок — проверить фильтры, возможно слишком строгие")

    return "\n".join(lines)


def main():
    logging.basicConfig(level=logging.INFO)
    report = build_weekly_report()
    print(report)

    # Save to file
    out_path = ROOT / "models" / "weekly_report.txt"
    out_path.write_text(report, encoding="utf-8")
    logger.info("Weekly report saved to %s", out_path)

    # Send to Telegram if configured
    try:
        from scripts.telegram_utils import send_telegram_message

        send_telegram_message(report)
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)


if __name__ == "__main__":
    main()
