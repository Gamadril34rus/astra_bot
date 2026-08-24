#!/usr/bin/env python3
"""CLI аудита торговых мозгов и формализованных стратегий ASTRA.

Обеспечивает быстрый многолетний аудит без проведения ML-обучения и без
выставления реальных ордеров.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from astra_bot.decision.strategy_registry import STRATEGY_REGISTRY, TIER_RESEARCH

from strategy_lab import CANDIDATES, atr, run_engine


def main() -> int:
    parser = argparse.ArgumentParser(description="Многолетний аудит торговых мозгов ASTRA")
    parser.add_argument("--data", default="data/BTCUSDT_4h.csv", help="Путь к CSV файлу свечей")
    parser.add_argument("--symbol", default="BTC/USDT", help="Символ торговой пары")
    parser.add_argument("--timeframe", default="4h", help="Таймфрейм свечей")
    parser.add_argument("--start", default="2021-01-01", help="Дата начала YYYY-MM-DD")
    parser.add_argument("--oos-start", default="2025-01-01", help="Дата начала OOS YYYY-MM-DD")
    parser.add_argument("--end", default="2026-08-22", help="Дата окончания YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=10000.0, help="Начальный капитал USDT")
    parser.add_argument("--out", default="reports/brain_audit/brain_audit.json", help="Выходной JSON отчёт")
    args = parser.parse_args()

    data_path = PROJECT_ROOT / args.data
    if not data_path.exists():
        print(f"Ошибка: файл данных {data_path} не найден.", file=sys.stderr)
        return 1

    df = pd.read_csv(data_path)
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)

    t0_ms = int(datetime.fromisoformat(args.start).replace(tzinfo=UTC).timestamp() * 1000)
    toos_ms = int(datetime.fromisoformat(args.oos_start).replace(tzinfo=UTC).timestamp() * 1000)
    t1_ms = int(datetime.fromisoformat(args.end).replace(tzinfo=UTC).timestamp() * 1000)

    # Клиппинг границ по доступным в CSV данным
    data_min_ms = df["open_time"].min()
    data_max_ms = df["open_time"].max()

    t0_ms = max(t0_ms, data_min_ms)
    t1_ms = min(t1_ms, data_max_ms)
    if toos_ms <= t0_ms or toos_ms >= t1_ms:
        toos_ms = t0_ms + (t1_ms - t0_ms) // 2

    atr_vals = atr(df, 14)

    audit_results = {
        "metadata": {
            "symbol": args.symbol,
            "timeframe": args.timeframe,
            "data_file": args.data,
            "start": str(pd.to_datetime(t0_ms, unit="ms", utc=True)),
            "oos_start": str(pd.to_datetime(toos_ms, unit="ms", utc=True)),
            "end": str(pd.to_datetime(t1_ms, unit="ms", utc=True)),
            "capital": args.capital,
            "executed_at": str(datetime.now(UTC)),
        },
        "strategies": {},
    }

    # Маппинг ключей из реестра в кандидатов лабораторного движка
    cand_map = {c["key"]: c for c in CANDIDATES}
    # Дополнительный алиас для ts_momentum -> tsm45_ls и ts_momentum_adx -> tsm45_adx
    cand_map["ts_momentum"] = cand_map.get("tsm45_ls")
    cand_map["ts_momentum_adx"] = cand_map.get("tsm45_adx")

    for key, entry in STRATEGY_REGISTRY.items():
        if entry.tier == TIER_RESEARCH or key not in cand_map or cand_map[key] is None:
            audit_results["strategies"][key] = {
                "name": entry.name,
                "tier": entry.tier,
                "status": "not_auditable",
                "reason": entry.execution_blocked_reason,
            }
            continue

        cand = cand_map[key]
        desired = cand["fn"](df)

        def eval_window(ms_start, ms_end):
            m = (df["open_time"] >= ms_start) & (df["open_time"] < ms_end)
            if m.sum() <= 1:
                return {"trades": 0, "win_rate": 0.0, "pf": 0.0, "pnl": 0.0, "return_pct": 0.0, "max_dd": 0.0, "expectancy": 0.0}
            res = run_engine(
                df[m].reset_index(drop=True),
                desired[m].reset_index(drop=True),
                cand["stop_mult"],
                cand["take_mult"],
                cand["max_hold"],
                capital=args.capital,
                atr_values=atr_vals[m].reset_index(drop=True),
                long_only=cand["long_only"],
                vol_target=cand["vol_target"],
            )
            pnl = (res.ret_pct / 100.0) * args.capital
            exp = pnl / res.trades if res.trades > 0 else 0.0
            return {
                "trades": res.trades,
                "win_rate": round(res.win_rate, 2),
                "pf": round(res.pf if res.pf != float("inf") else 999.0, 2),
                "pnl": round(pnl, 2),
                "return_pct": round(res.ret_pct, 2),
                "max_dd": round(res.max_dd, 2),
                "expectancy": round(exp, 2),
            }

        is_metrics = eval_window(t0_ms, toos_ms)
        oos_metrics = eval_window(toos_ms, t1_ms)
        full_metrics = eval_window(t0_ms, t1_ms)

        audit_results["strategies"][key] = {
            "name": entry.name,
            "tier": entry.tier,
            "status": "audited",
            "in_sample": is_metrics,
            "out_of_sample": oos_metrics,
            "full_window": full_metrics,
        }

    out_file = PROJECT_ROOT / args.out
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(audit_results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Аудит завершён. Результаты сохранены в: {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
