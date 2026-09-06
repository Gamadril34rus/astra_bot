"""
Adaptive Analysis — Block 7: адаптация весов стратегий на основе PnL.

- Читает trades из JSONL + DB
- Считает win rate, PF, avg R по стратегиям
- Обновляет data/weights.json: увеличивает вес прибыльным, уменьшает убыточным
- Отключает стратегии с 3+ неделями убытка
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


def _parse_trades(days: int = 21) -> list[dict[str, Any]]:
    """Parse trades for last N days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    trades: list[dict[str, Any]] = []
    seen: set[str] = set()

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
                # Filter by time
                closed = obj.get("closed_at")
                if closed is not None:
                    try:
                        ts = int(closed)
                        if ts < 10000000000:
                            ts = ts * 1000
                        if ts < cutoff_ms:
                            continue
                    except Exception:
                        pass
                trades.append(obj)
            except Exception:
                continue

    db_path = ROOT / "data" / "trades.db"
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute(
                "SELECT id, symbol, direction, pnl, strategy, regime, closed_at FROM trades WHERE closed_at >= ?",
                (cutoff_ms,),
            )
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
                        "pnl": row[3],
                        "strategy": row[4],
                        "regime": row[5],
                        "closed_at": row[6],
                    }
                )
            conn.close()
        except Exception as e:
            logger.warning("DB parse failed: %s", e)

    return trades


def _compute_strategy_stats(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_strat: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        strat = t.get("strategy") or "unknown"
        pnl = float(t.get("pnl") or 0)
        by_strat[strat].append(pnl)

    stats: dict[str, dict[str, Any]] = {}
    for strat, pnls in by_strat.items():
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        total_pnl = sum(pnls)
        win_rate = len(wins) / len(pnls) if pnls else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        pf = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else (float("inf") if wins else 0)
        stats[strat] = {
            "count": len(pnls),
            "pnl": total_pnl,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "pf": pf if pf != float("inf") else 999.0,
            "wins": len(wins),
            "losses": len(losses),
        }
    return stats


def adapt_weights() -> dict[str, Any]:
    """Main adaptation logic."""
    # Load current weights
    wpath = ROOT / "data" / "weights.json"
    if wpath.exists():
        try:
            current = json.loads(wpath.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    else:
        current = {}

    strategy_weights = current.get("strategy_weights") or {
        "trend_following": 1.0,
        "mean_reversion": 1.0,
        "breakout": 1.0,
        "momentum": 1.0,
        "scalp": 0.8,
        "scalp5m": 0.8,
        "pullback": 0.9,
        "ts_momentum": 0.9,
        "ts_momentum_adx": 0.8,
    }

    trades = _parse_trades(days=21)
    if len(trades) < 10:
        logger.info("Not enough trades for adaptation (%d < 10), keeping current weights", len(trades))
        return current

    stats = _compute_strategy_stats(trades)

    # Adaptation rules per Block 7:
    # - If strategy profitable (PF > 1.2, WR > 50%) -> increase weight 10%
    # - If losing (PF < 0.8 or PnL <0 with >10 trades) -> decrease 20%
    # - If 3 weeks losing (we have 21 days) and PnL <0 -> disable
    new_weights = dict(strategy_weights)
    disabled = set(current.get("disabled_strategies") or [])

    for strat, data in stats.items():
        if strat not in new_weights:
            new_weights[strat] = 1.0

        count = data["count"]
        pnl = data["pnl"]
        pf = data["pf"]
        wr = data["win_rate"]

        if count < 5:
            continue  # Not enough data

        if pnl > 0 and pf > 1.2 and wr > 0.5:
            # Profitable -> +10%
            new_weights[strat] = min(2.0, new_weights[strat] * 1.1)
            logger.info("Boost %s: PF %.2f WR %.0f%% -> %.2f", strat, pf, wr * 100, new_weights[strat])
        elif pnl < 0 and (pf < 0.8 or count >= 10):
            # Losing -> -20%
            new_weights[strat] = max(0.1, new_weights[strat] * 0.8)
            logger.info("Reduce %s: PF %.2f PnL %.2f -> %.2f", strat, pf, pnl, new_weights[strat])
            # If losing for 3 weeks (21 days) and still negative with >=15 trades -> disable
            if count >= 15 and pnl < 0:
                if new_weights[strat] < 0.3:
                    disabled.add(strat)
                    logger.warning("Disabling strategy %s: 21d PnL %.2f, weight %.2f", strat, pnl, new_weights[strat])
        # Re-enable if previously disabled but now profitable
        if strat in disabled and pnl > 0 and pf > 1.5:
            disabled.discard(strat)
            new_weights[strat] = max(new_weights[strat], 0.5)
            logger.info("Re-enabling %s: now profitable", strat)

    # Normalize weights to keep average ~1.0
    if new_weights:
        avg = sum(new_weights.values()) / len(new_weights)
        if avg != 0:
            # Normalize to avg 1.0
            for k in new_weights:
                new_weights[k] = round(new_weights[k] / avg, 3)

    result = {
        "strategy_weights": new_weights,
        "disabled_strategies": sorted(list(disabled)),
        "last_adaptation": datetime.now(timezone.utc).isoformat(),
        "stats_21d": stats,
        "total_trades_21d": len(trades),
    }

    # Save
    wpath.parent.mkdir(parents=True, exist_ok=True)
    wpath.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Weights adapted and saved to %s", wpath)

    # Also try to save via state_manager
    try:
        from astra_bot.data.state_manager import get_state_manager

        sm = get_state_manager()
        sm.save_weights(result)
    except Exception as e:
        logger.warning("StateManager save_weights failed: %s", e)

    return result


def main():
    logging.basicConfig(level=logging.INFO)
    result = adapt_weights()
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
