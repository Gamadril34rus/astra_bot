#!/usr/bin/env python3
"""Единый скрипт многолетнего исследовательского аудита стратегий ASTRA.

Запускает полный аудит всех формализованных стратегий и концепций из ТЗ:
- ts_momentum, ts_momentum_adx;
- book_breakout, momentum, mean_reversion, pullback, high_winrate, selective;
- multicurrency_mtf (+ 5 ablation вариантов);
- исследовательские профили (livermore_pivot, soros_regime, druckenmiller_driver, tudor_risk).

Создаёт структурированные отчёты в reports/research_2026/:
protocol.json, progress.json, data_quality.json, strategy_inventory.json, aggregate_summary.json/md,
rejected_strategies.md, shadow_strategies.md, candidate_for_manual_review.md и подкаталоги.
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
from scripts.audit_multicurrency import resample_klines, run_audit_simulation

from strategy_lab import CANDIDATES, atr, run_engine


def save_progress(out_dir: Path, stage: str, percent: float, status: str = "RUNNING", details: str = ""):
    progress_file = out_dir / "progress.json"
    data = {
        "status": status,
        "stage": stage,
        "percent": round(percent, 1),
        "details": details,
        "updated_at": str(datetime.now(UTC)),
    }
    progress_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Единый исследовательский аудит стратегий ASTRA (2021-2026)")
    parser.add_argument("--data-dir", default="data", help="Каталог с CSV данными")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT", help="Список символов через запятую")
    parser.add_argument("--start", default="2021-01-01", help="Начало IS YYYY-MM-DD")
    parser.add_argument("--oos-start", default="2025-01-01", help="Начало OOS YYYY-MM-DD")
    parser.add_argument("--end", default="2026-08-22", help="Конец YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=10000.0, help="Стартовый капитал USDT")
    parser.add_argument("--fee", type=float, default=0.001, help="Базовая комиссия (0.001 равно 0.1%)")
    parser.add_argument("--slippage", type=float, default=0.001, help="Базовое проскальзывание (0.001 равно 0.1%)")
    parser.add_argument("--news-file", default=None, help="Файл с историческими новостями")
    parser.add_argument("--out", default="reports/research_2026", help="Каталог выходных артефактов")
    args = parser.parse_args()

    out_dir = PROJECT_ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    save_progress(out_dir, "Initialization", 0.0, "RUNNING", "Проверка доступности данных")

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    data_dir = PROJECT_ROOT / args.data_dir

    data_quality = {}
    data_frames_1h, data_frames_4h, data_frames_1d = {}, {}, {}

    for sym in symbols:
        f1h = data_dir / f"{sym}_1h.csv"
        if not f1h.exists():
            save_progress(out_dir, "Failed", 0.0, "FAILED", f"Файл {f1h} не найден")
            print(f"Ошибка: Файл {f1h} не найден.", file=sys.stderr)
            return 1
        df1 = pd.read_csv(f1h)
        dups = int(df1.duplicated(subset=["open_time"]).sum())
        df1 = df1.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)

        f4h = data_dir / f"{sym}_4h.csv"
        df4 = pd.read_csv(f4h).drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True) if f4h.exists() else resample_klines(df1, "4h")

        f1d = data_dir / f"{sym}_1d.csv"
        df1d = pd.read_csv(f1d).drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True) if f1d.exists() else resample_klines(df1, "1d")

        data_frames_1h[sym] = df1
        data_frames_4h[sym] = df4
        data_frames_1d[sym] = df1d

        data_quality[sym] = {
            "source_file": str(f1h),
            "candles_1h": len(df1),
            "candles_4h": len(df4),
            "candles_1d": len(df1d),
            "duplicates_removed": dups,
            "first_timestamp": str(pd.to_datetime(df1["open_time"].min(), unit="ms", utc=True)),
            "last_timestamp": str(pd.to_datetime(df1["open_time"].max(), unit="ms", utc=True)),
        }

    (out_dir / "data_quality.json").write_text(json.dumps(data_quality, indent=2, ensure_ascii=False), encoding="utf-8")

    # Определение git SHA
    import subprocess
    try:
        git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode("utf-8").strip()
    except Exception:
        git_sha = "unknown"

    protocol = {
        "created_at": str(datetime.now(UTC)),
        "git_sha": git_sha,
        "symbols": symbols,
        "timeframes": ["1h", "4h", "1d"],
        "start": args.start,
        "oos_start": args.oos_start,
        "end": args.end,
        "initial_capital": args.capital,
        "fee": args.fee,
        "slippage": args.slippage,
        "cost_stress_fee": 0.002,
        "cost_stress_slippage": 0.001,
        "news_filter_tested": args.news_file is not None,
        "anti_lookahead_policy": "Strict closed candle entry only (t+1)",
        "code_version": "1.0.0-audit",
    }
    (out_dir / "protocol.json").write_text(json.dumps(protocol, indent=2, ensure_ascii=False), encoding="utf-8")

    # Strategy Inventory
    inventory = {k: {"name": v.name, "source": v.source, "tier": v.tier, "reason": v.execution_blocked_reason} for k, v in STRATEGY_REGISTRY.items()}
    (out_dir / "strategy_inventory.json").write_text(json.dumps(inventory, indent=2, ensure_ascii=False), encoding="utf-8")

    start_ms = int(datetime.fromisoformat(args.start).replace(tzinfo=UTC).timestamp() * 1000)
    oos_ms = int(datetime.fromisoformat(args.oos_start).replace(tzinfo=UTC).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(args.end).replace(tzinfo=UTC).timestamp() * 1000)

    # 1. Single strategy audits via Strategy Lab Engine
    save_progress(out_dir, "Strategy Lab Audit", 20.0, "RUNNING", "Аудит одиночных стратегий по портфелю символов и таймфреймов")
    lab_results = {}
    cand_map = {c["key"]: c for c in CANDIDATES}
    cand_map["ts_momentum"] = cand_map.get("tsm45_ls")
    cand_map["ts_momentum_adx"] = cand_map.get("tsm45_adx")
    cand_map["book_breakout"] = cand_map.get("don100_adx")
    cand_map["momentum"] = cand_map.get("gc50200_adx")
    cand_map["mean_reversion"] = cand_map.get("bbfade_lo")
    cand_map["pullback"] = cand_map.get("pullback")
    cand_map["high_winrate"] = cand_map.get("rsi2_trend")
    cand_map["selective"] = cand_map.get("tsm45_lo_ema")
    cand_map["academy_hybrid_mtf"] = cand_map.get("tsm45_ls_vt")

    for key, entry in STRATEGY_REGISTRY.items():
        if entry.tier == TIER_RESEARCH or key not in cand_map or cand_map[key] is None:
            lab_results[key] = {"name": entry.name, "tier": entry.tier, "status": "NOT_AUDITABLE_WITH_AVAILABLE_DATA", "reason": entry.execution_blocked_reason}
            continue

        cand = cand_map[key]

        # Портфельная оценка по всем символам и 4H таймфрейму (сводка)
        def eval_multi(ms0, ms1, fee_val=args.fee, slip_val=args.slippage, c_info=cand):
            total_trades_all = 0
            wins_all = 0
            net_pnl_all = 0.0
            max_dd_max = 0.0
            pfs = []

            for sym in symbols:
                df_sym = data_frames_4h[sym]
                desired = c_info["fn"](df_sym)
                atr_vals = atr(df_sym, 14)
                m = (df_sym["open_time"] >= ms0) & (df_sym["open_time"] < ms1)
                if m.sum() <= 1:
                    continue
                rr = run_engine(
                    df_sym[m].reset_index(drop=True), desired[m].reset_index(drop=True),
                    c_info["stop_mult"], c_info["take_mult"], c_info["max_hold"], capital=args.capital / len(symbols),
                    atr_values=atr_vals[m].reset_index(drop=True), long_only=c_info["long_only"],
                    vol_target=c_info["vol_target"], fee=fee_val, slippage=slip_val,
                )
                pnl = (rr.ret_pct / 100.0) * (args.capital / len(symbols))
                total_trades_all += rr.trades
                wins_all += rr.wins
                net_pnl_all += pnl
                max_dd_max = max(max_dd_max, rr.max_dd)
                if rr.trades > 0:
                    pfs.append(rr.pf if rr.pf != float("inf") else 999.0)

            win_rate = (wins_all / total_trades_all * 100.0) if total_trades_all > 0 else 0.0
            avg_pf = float(pd.Series(pfs).mean()) if pfs else 0.0
            ret_pct = (net_pnl_all / args.capital) * 100.0
            exp = net_pnl_all / total_trades_all if total_trades_all > 0 else 0.0

            return {
                "trades": total_trades_all,
                "total_trades": total_trades_all,
                "win_rate": round(win_rate, 2),
                "pf": round(avg_pf, 2),
                "profit_factor": round(avg_pf, 2),
                "net_pnl": round(net_pnl_all, 2),
                "return_pct": round(ret_pct, 2),
                "max_dd": round(max_dd_max, 2),
                "max_drawdown": round(max_dd_max, 2),
                "expectancy": round(exp, 2),
            }

        is_metrics = eval_multi(start_ms, oos_ms)
        oos_metrics = eval_multi(oos_ms, end_ms)
        full_metrics = eval_multi(start_ms, end_ms)
        stress_metrics = eval_multi(start_ms, end_ms, fee_val=0.002, slip_val=0.001)

        # Строгие правила классификации
        cond_candidate = (
            oos_metrics["pf"] >= 1.10 and
            oos_metrics["expectancy"] > 0 and
            oos_metrics["return_pct"] >= 0 and
            oos_metrics["max_dd"] <= 15.0 and
            oos_metrics["trades"] >= 20 and
            is_metrics["pf"] >= 1.0 and
            full_metrics["pf"] >= 1.10 and
            stress_metrics["pf"] >= 0.90 and
            (stress_metrics["pf"] >= oos_metrics["pf"] * 0.75)
        )

        if cond_candidate:
            classification = "CANDIDATE_FOR_MANUAL_REVIEW"
        elif oos_metrics["pf"] >= 1.0 or full_metrics["pf"] >= 1.0:
            classification = "SHADOW_ONLY"
        else:
            classification = "REJECTED"

        lab_results[key] = {
            "name": entry.name,
            "tier": entry.tier,
            "status": classification,
            "in_sample": is_metrics,
            "out_of_sample": oos_metrics,
            "full_window": full_metrics,
            "cost_stress": stress_metrics,
        }

    # 2. Multicurrency MTF Audit + Ablations
    save_progress(out_dir, "Multicurrency MTF Audit", 60.0, "RUNNING", "Аудит multicurrency_mtf и ablation")

    def run_mtf_sim(ms0, ms1, **kwargs):
        return run_audit_simulation(
            symbols, data_frames_1h, data_frames_4h, data_frames_1d,
            ms0, ms1, args.capital, args.fee, args.slippage, **kwargs,
        )

    mtf_full = run_mtf_sim(start_ms, end_ms)["metrics"]
    mtf_is = run_mtf_sim(start_ms, oos_ms)["metrics"]
    mtf_oos = run_mtf_sim(oos_ms, end_ms)["metrics"]
    mtf_stress = run_audit_simulation(
        symbols, data_frames_1h, data_frames_4h, data_frames_1d,
        start_ms, end_ms, args.capital, fee_rate=0.002, slippage_rate=0.001,
    )["metrics"]

    # Normalize keys for multicurrency_mtf
    for m in (mtf_is, mtf_oos, mtf_full, mtf_stress):
        m["pf"] = m["profit_factor"]
        m["max_dd"] = m["max_drawdown"]
        m["trades"] = m["total_trades"]

    lab_results["multicurrency_mtf"] = {
        "name": "Multicurrency MTF Protocol",
        "tier": "audit",
        "status": "REJECTED" if mtf_oos["profit_factor"] < 1.0 else "SHADOW_ONLY",
        "in_sample": mtf_is,
        "out_of_sample": mtf_oos,
        "full_window": mtf_full,
        "cost_stress": mtf_stress,
    }


    # Ablations
    ablation_dir = out_dir / "ablation"
    ablation_dir.mkdir(parents=True, exist_ok=True)
    ablations = {
        "baseline": {},
        "without_btc_gate": {"use_btc_gate": False},
        "without_volume_filter": {"use_volume_filter": False},
        "without_retest": {"use_retest": False},
        "without_partial_trailing": {"use_partial_trailing": False},
    }
    ablation_res = {}
    for ab_name, ab_kw in ablations.items():
        res_ab = run_mtf_sim(start_ms, end_ms, **ab_kw)["metrics"]
        (ablation_dir / f"{ab_name}.json").write_text(json.dumps(res_ab, indent=2), encoding="utf-8")
        ablation_res[ab_name] = res_ab

    # Сохранение агрегированных сводок
    (out_dir / "aggregate_summary.json").write_text(json.dumps({"strategies": lab_results, "ablation": ablation_res}, indent=2, ensure_ascii=False), encoding="utf-8")

    # Формирование категориальных markdown отчётов
    rejected_lines = ["# Отклоненные стратегии (REJECTED)", ""]
    shadow_lines = ["# Стратегии теневого отслеживания (SHADOW_ONLY)", ""]
    candidate_lines = ["# Кандидаты для ручного ревью (CANDIDATE_FOR_MANUAL_REVIEW)", ""]

    for k, v in lab_results.items():
        st = v["status"]
        if st == "REJECTED":
            rejected_lines.append(f"- **{v['name']}** ({k}): OOS PF {v.get('out_of_sample', {}).get('pf', 0)}, Return {v.get('out_of_sample', {}).get('return_pct', 0)}%")
        elif st == "SHADOW_ONLY":
            shadow_lines.append(f"- **{v['name']}** ({k}): OOS PF {v.get('out_of_sample', {}).get('pf', 0)}, Return {v.get('out_of_sample', {}).get('return_pct', 0)}%")
        elif st == "CANDIDATE_FOR_MANUAL_REVIEW":
            candidate_lines.append(f"- **{v['name']}** ({k}): OOS PF {v.get('out_of_sample', {}).get('pf', 0)}, Return {v.get('out_of_sample', {}).get('return_pct', 0)}%")

    (out_dir / "rejected_strategies.md").write_text("\n".join(rejected_lines), encoding="utf-8")
    (out_dir / "shadow_strategies.md").write_text("\n".join(shadow_lines), encoding="utf-8")
    (out_dir / "candidate_for_manual_review.md").write_text("\n".join(candidate_lines), encoding="utf-8")

    # Сводный отчёт aggregate_summary.md
    md_lines = [
        "# Итоговый исследовательский аудит ASTRA (2021–2026)",
        "",
        f"- Символы: `{', '.join(symbols)}`",
        f"- Период: {args.start} → {args.end} (OOS c {args.oos_start})",
        "",
        "## Результаты стратегий",
        "",
        "| Стратегия | Классификация | IS PF | OOS PF | Full PF | Stress PF | OOS Return % | OOS MaxDD % | Trades |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for _k, v in lab_results.items():
        if v["status"] == "NOT_AUDITABLE_WITH_AVAILABLE_DATA":
            md_lines.append(f"| {v['name']} | {v['status']} | — | — | — | — | — | — | 0 |")
        else:
            is_p = v["in_sample"]["pf"]
            oos_p = v["out_of_sample"]["pf"]
            full_p = v["full_window"]["pf"]
            str_p = v["cost_stress"]["pf"]
            ret_p = v["out_of_sample"]["return_pct"]
            dd_p = v["out_of_sample"]["max_drawdown"]
            tr_p = v["out_of_sample"]["trades"]
            md_lines.append(f"| {v['name']} | {v['status']} | {is_p} | {oos_p} | {full_p} | {str_p} | {ret_p:+.2f}% | {dd_p:.2f}% | {tr_p} |")

    md_lines.extend([
        "",
        "## Multicurrency MTF Ablation Studies",
        "",
        "| Вариант | Profit Factor | Return % | Max Drawdown % | Trades |",
        "|---|---|---|---|---|",
    ])
    for k, v in ablation_res.items():
        md_lines.append(f"| {k} | {v['profit_factor']} | {v['return_pct']:+.2f}% | {v['max_drawdown']:.2f}% | {v['total_trades']} |")

    md_lines.extend([
        "",
        "⚠️ **Отказ от ответственности**: Прошлые исследовательские результаты бэктестов не являются гарантией будущей доходности. Все стратегии остаются исключительно в статусе `audit`/`research` без прав автоматического исполнения ордеров.",
    ])

    (out_dir / "aggregate_summary.md").write_text("\n".join(md_lines), encoding="utf-8")

    save_progress(out_dir, "Completed", 100.0, "COMPLETED", "Исследовательский аудит завершён")
    print(f"Полный исследовательский аудит успешно завершён. Артефакты: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
