#!/usr/bin/env python3
"""Read-only replay of saved paper trades for the hypothesis-race audit.

The script never writes under models/. It aggregates partial close rows by
position id, applies pre-declared policies, and prints JSON suitable for an
audit report. This is a counterfactual on observed trades, not an edge test.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any, Callable

DEAD = {
    "momentum", "double_top_bottom", "cup_and_handle", "scalp",
    "trend_following", "flag", "pullback",
}


def load_positions(path: Path) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            grouped[str(row["id"])].append(row)
    out = []
    for position_id, legs in grouped.items():
        first = min(legs, key=lambda x: x["opened_at"])
        out.append({
            **first,
            "id": position_id,
            "closed_at": max(x["closed_at"] for x in legs),
            "r_multiple": sum(float(x["r_multiple"]) for x in legs),
            "pnl": sum(float(x["pnl"]) for x in legs),
            "fees": sum(float(x.get("fees", 0)) for x in legs),
            "funding": sum(float(x.get("funding", 0)) for x in legs),
            "mfe_r": max(float(x.get("mfe_r", 0)) for x in legs),
            "mae_r": max(float(x.get("mae_r", 0)) for x in legs),
            "exit_reasons": [str(x["exit_reason"]) for x in legs],
            "legs": len(legs),
        })
    return sorted(out, key=lambda x: (x["opened_at"], x["id"]))


def pf(rows: list[dict[str, Any]], weight: Callable[[dict[str, Any]], float] = lambda _: 1.0) -> float:
    wins = sum(max(0.0, x["r_multiple"] * weight(x)) for x in rows)
    losses = -sum(min(0.0, x["r_multiple"] * weight(x)) for x in rows)
    return wins / losses if losses else math.inf


def bootstrap_mean_ci(values: list[float], seed: int = 20260913, reps: int = 20_000) -> list[float]:
    """IID position bootstrap; descriptive only because trades are dependent."""
    rng = random.Random(seed)
    n = len(values)
    draws = sorted(mean(rng.choices(values, k=n)) for _ in range(reps))
    return [draws[int(0.025 * reps)], draws[int(0.975 * reps) - 1]]


def strategy_table(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    by_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_strategy[row["strategy"]].append(row)
    return {
        strategy: {
            "n": len(group), "sum_r": sum(x["r_multiple"] for x in group),
            "mean_r": mean(x["r_multiple"] for x in group), "pf": pf(group),
        }
        for strategy, group in sorted(by_strategy.items())
    }


def exposure_by_day(rows: list[dict[str, Any]], weight: Callable[[dict[str, Any]], float]) -> dict[str, Any]:
    start = datetime.fromtimestamp(min(x["opened_at"] for x in rows) / 1000, UTC).date()
    end = datetime.fromtimestamp(max(x["closed_at"] for x in rows) / 1000, UTC).date()
    result = {}
    day = start
    while day <= end:
        lo = datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000
        hi = lo + 86_400_000
        position_hours = 0.0
        events = []
        for row in rows:
            overlap = max(0, min(row["closed_at"], hi) - max(row["opened_at"], lo))
            w = weight(row)
            position_hours += overlap / 3_600_000 * w
            if overlap:
                events += [(max(row["opened_at"], lo), w), (min(row["closed_at"], hi), -w)]
        current = peak = 0.0
        # Close before open at equal timestamps.
        for _, delta in sorted(events, key=lambda e: (e[0], e[1])):
            current += delta
            peak = max(peak, current)
        result[str(day)] = {"weighted_position_hours": position_hours, "peak_weighted_positions": peak}
        day += timedelta(days=1)
    return result


def replay(rows: list[dict[str, Any]], weight: Callable[[dict[str, Any]], float], initial: float,
           risk_fraction: float, max_positions: int) -> dict[str, float]:
    """Replay entries with a concurrency cap; P&L = equity*risk_fraction*R*weight."""
    accepted = []
    active: list[dict[str, Any]] = []
    for row in rows:
        active = [x for x in active if x["closed_at"] > row["opened_at"]]
        if weight(row) <= 0 or len(active) >= max_positions:
            continue
        accepted.append(row)
        active.append(row)
    equity = peak = initial
    max_dd_money = 0.0
    cum_r = peak_r = max_dd_r = 0.0
    for row in sorted(accepted, key=lambda x: (x["closed_at"], x["id"])):
        wr = row["r_multiple"] * weight(row)
        cum_r += wr
        peak_r = max(peak_r, cum_r)
        max_dd_r = max(max_dd_r, peak_r - cum_r)
        equity += equity * risk_fraction * wr
        peak = max(peak, equity)
        max_dd_money = max(max_dd_money, peak - equity)
    return {
        "n": len(accepted), "sum_r": cum_r, "max_drawdown_r": max_dd_r,
        "ending_equity": equity, "pnl": equity - initial,
        "max_drawdown_money": max_dd_money,
        "max_drawdown_pct": 100 * max_dd_money / peak if peak else 0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", type=Path, default=Path("models/paper_trades.jsonl"))
    ap.add_argument("--state", type=Path, default=Path("models/paper_positions.json"))
    args = ap.parse_args()
    rows = load_positions(args.trades)
    state = json.loads(args.state.read_text(encoding="utf-8"))
    initial = float(state["initial_capital"])
    stats = strategy_table(rows)
    small = {s for s, v in stats.items() if v["n"] < 5}
    policies = {
        "baseline": lambda x: 1.0,
        "honest_kill": lambda x: 0.0 if x["strategy"] in DEAD else 1.0,
        "honest_kill_quarter_n_lt_5": lambda x: 0.0 if x["strategy"] in DEAD else (0.25 if x["strategy"] in small else 1.0),
    }
    summaries = {}
    for name, weight in policies.items():
        kept = [x for x in rows if weight(x) > 0]
        vals = [x["r_multiple"] * weight(x) for x in kept]
        summaries[name] = {
            "n": len(kept), "sum_r": sum(vals), "mean_weighted_r_per_traded_position": mean(vals),
            "pf": pf(kept, weight), "bootstrap_95_ci_mean_r": bootstrap_mean_ci(vals),
        }
    scales = {"current_half_capital_max3": (0.005, 3), "full_capital_max5": (0.01, 5)}
    curves = {p: {s: replay(rows, w, initial, risk, cap) for s, (risk, cap) in scales.items()}
              for p, w in policies.items()}
    print(json.dumps({
        "input": {"rows": sum(x["legs"] for x in rows), "position_ids": len(rows), "initial_capital": initial},
        "strategy_stats": stats,
        "summaries": summaries,
        "quarter_budget_exposure_utc": exposure_by_day(rows, policies["honest_kill_quarter_n_lt_5"]),
        "curves": curves,
        "h3": {"computable": False, "reason": "saved closed trades contain no stop_loss, take_profit, confidence, ml_probability, or decision-time prior"},
        "h4": {"mfe_ge_1_position_ids": sum(x["mfe_r"] >= 1 for x in rows),
               "with_tp1_leg": sum(x["mfe_r"] >= 1 and "tp1" in x["exit_reasons"] for x in rows)},
        "assumptions": [
            "Partial rows are summed to one position-level R and PnL.",
            "Kill list is fixed before replay; this is a policy counterfactual, not causal proof.",
            "Current scale risks 0.5% equity per 1R; full scale risks 1.0%; PnL compounds at closes.",
            "IID bootstrap ignores temporal/cross-symbol dependence and is descriptive only.",
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
