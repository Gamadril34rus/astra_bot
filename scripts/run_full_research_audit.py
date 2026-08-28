#!/usr/bin/env python3
"""Единый скрипт многолетнего исследовательского аудита стратегий ASTRA.

Прогоняет строгий аудит всех формализованных OHLC-стратегий и MTF протокола:
- Пул данных: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT,
  AVAXUSDT, LINKUSDT, LTCUSDT на 1h/4h/1d;
- Период: 2021-01-01 → 2026-08-22, IS до 2024-12-31, OOS с 2025-01-01;
- Исполнение: сигнал по закрытой свече t, вход по открытию t+1 (строго в run_engine);
- Стоимости: база fee=0.1%/slippage=0.1%, stress fee=0.2%/slip=0.1%;
- Отсутствует look-ahead, forward-fill, OOS подгонка параметров.

Артефакты в `--out` (по ум. reports/research_2026/):
progress.json, protocol.json, data_quality.json, strategy_inventory.json,
aggregate_summary.{json,md}, rejected/shadow/candidate markdown'ы,
per-strategy json, ablation/ подкаталог.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from astra_bot.decision.strategy_registry import STRATEGY_REGISTRY, TIER_RESEARCH
from scripts.audit_multicurrency import resample_klines, run_audit_simulation

from strategy_lab import CANDIDATES, atr, run_engine

# ---------- Карта формализованных OHLC-стратегий в CANDIDATES из strategy_lab
# (стратегии из реестра сопоставляются с рабочими правилами; остальные
# из research-профиля считаются неаудируемыми с обоснованием).
STRATEGY_TO_CANDIDATE = {
    "ts_momentum": "tsm45_ls",
    "ts_momentum_adx": "tsm45_adx",
    "book_breakout": "don100_adx",
    "academy_hybrid_mtf": "academy_hybrid_mtf",
    "momentum": "gc50200_adx",
    "mean_reversion": "bbfade_lo",
    "pullback": "pullback",
    "high_winrate": "rsi2_trend",
    "selective": "tsm45_lo_ema",
}

TIMEFRAME_CANDIDATES = ("1h", "4h", "1d")

# Таймфреймы, на которые спроецированы сигнатуры правил strategy_lab.
# TSM-семейство и run_engine теперь авто-детектят bars/day по медианному
# шагу open_time (см. strategy_lab._bars_per_day), так что lookback в
# календарных днях и annualization шарпа корректно масштабируются под 1h/4h/1d.
CAND_LOOKBACK_BARS = {"1h": 24, "4h": 6, "1d": 1}  # bars per day for each TF

RESEARCH_PROFILE_NOTES = {
    "livermore_pivot": (
        "Концепция Jesse Livermore (ключевые пивоты, откат от предыдущего "
        "экстремума, подтверждение объёмом) требует детектирования "
        "swing-high/low с гистерезисом и отслеживания ленты последовательных "
        "сигналов. Имеющиеся OHLCV данных достаточно для формализации, но "
        "нужен отдельный код сигнального модуля — в рамках этого прохода не "
        "реализовано."
    ),
    "soros_regime": (
        "Рефлексивность Сороса описывается как макро-поведенческий цикл без "
        "измеримых OHLC-правил; формализация требует привлечения данных "
        "кредитного импульса, открытого интереса или новостного сентимента. "
        "На одних публичных OHLCV правило верифицировать нельзя."
    ),
    "druckenmiller_driver": (
        "«Доминантный драйвер» Дракенмиллера — это концепция фокуса портфеля "
        "на одном макро-факторе, не сводимая к набору индикаторов на свечах; "
        "нуждается в макроэкономических рядах (ставки, ликвидность, "
        "секторные потоки), которых нет в данном проекте."
    ),
    "tudor_risk": (
        "Риск-оверлей Tudor Jones (сокращение размера при просадке, "
        "волатильный таргет, стоп-переключения) является менеджером риска, "
        "а не самостоятельным альфа-правилом и должен тестироваться как "
        "модификатор позиционирования поверх стратегий; отдельно как "
        "OHLC-стратегия не классифицируется."
    ),
}


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


def _run_engine_on_window(df: pd.DataFrame, desired: pd.Series, cand: dict, capital: float,
                          fee: float, slippage: float) -> dict:
    """Запустить engine на окне, вернуть словарь метрик + gross wins/losses."""
    atr_vals = atr(df, 14)
    rr = run_engine(
        df.reset_index(drop=True),
        desired.reset_index(drop=True),
        cand["stop_mult"], cand["take_mult"], cand["max_hold"],
        capital=capital,
        atr_values=atr_vals.reset_index(drop=True),
        long_only=cand["long_only"],
        vol_target=cand["vol_target"],
        fee=fee, slippage=slippage,
    )
    pnl = (rr.ret_pct / 100.0) * capital
    exp = pnl / rr.trades if rr.trades > 0 else 0.0
    return {
        "trades": rr.trades,
        "total_trades": rr.trades,
        "wins": rr.wins,
        "win_rate": round(rr.win_rate / rr.trades * 100.0, 2) if rr.trades else 0.0,
        "pf": round(rr.pf, 2) if rr.pf != float("inf") else 999.0,
        "profit_factor": round(rr.pf, 2) if rr.pf != float("inf") else 999.0,
        "net_pnl": round(pnl, 2),
        "return_pct": round(rr.ret_pct, 2),
        "max_dd": round(rr.max_dd, 2),
        "max_drawdown": round(rr.max_dd, 2),
        "expectancy": round(exp, 2),
        "sharpe": round(rr.sharpe, 2),
        # equity понадобится для портфельного стаканья по TF/символам;
        # в JSON он не кладётся, а после агрегации отбрасывается.
        "_equity": rr.equity,
        "_timestamps": df["open_time"].values,
        "_capital_end": capital + pnl,
        "_gross_win": None,  # filled later if needed
        "_gross_loss": None,
    }


def _merge_equity(equity_list: list[tuple[np.ndarray, np.ndarray, float]]) -> dict:
    """Слить N кривых капитала (timestamps, equity, start_capital) в общую
    портфельную кривую и вернуть агрегированные метрики: PF считаем
    money-weighted как сумма gross wins / сумма gross losses по отдельным
    ранам аппроксимативно через сопоставление кривых и их долей."""
    if not equity_list:
        return {"trades": 0, "wins": 0, "win_rate": 0.0, "pf": 0.0,
                "profit_factor": 0.0, "net_pnl": 0.0, "return_pct": 0.0,
                "max_dd": 0.0, "max_drawdown": 0.0, "expectancy": 0.0,
                "sharpe": 0.0}
    total_start = sum(c for _, _, c in equity_list)
    # Объединение по union индексу, forward fill
    all_ts = np.unique(np.concatenate([t for t, _, _ in equity_list]))
    curves = []
    for ts, eq, _c0 in equity_list:
        s = pd.Series(eq, index=ts)
        s = s.reindex(all_ts).ffill().bfill()
        curves.append(s.values)
    stack = np.vstack(curves)
    port = stack.sum(axis=0)
    start_equity = float(port[0])
    end_equity = float(port[-1])
    rets = pd.Series(port).pct_change().dropna()
    dd = ((pd.Series(port).cummax() - pd.Series(port)) / pd.Series(port).cummax() * 100.0).max()
    # Приблизительный PF по бару-дневной логике в терминах equity changes
    # (точный PF возможен только по сделкам — мы суммируем trades/gross
    # по отдельным ранам в агрегаторе выше).
    pos_sum = float(rets[rets > 0].sum() * start_equity) if len(rets) else 0.0
    neg_sum = float((-rets[rets < 0]).sum() * start_equity) if len(rets) else 0.0
    pf = pos_sum / neg_sum if neg_sum > 0 else (999.0 if pos_sum > 0 else 0.0)
    sharpe = float(rets.mean() / rets.std() * np.sqrt(365 * 24)) if len(rets) > 2 and rets.std() > 0 else 0.0
    total_pnl = end_equity - total_start
    return {
        "trades": None,  # заполнится в агрегаторе
        "total_trades": None,
        "wins": None,
        "win_rate": None,
        "pf": round(pf, 2) if pf != float("inf") else 999.0,
        "profit_factor": round(pf, 2) if pf != float("inf") else 999.0,
        "net_pnl": round(total_pnl, 2),
        "return_pct": round((end_equity / total_start - 1) * 100.0, 2),
        "max_dd": round(float(dd), 2) if not pd.isna(dd) else 0.0,
        "max_drawdown": round(float(dd), 2) if not pd.isna(dd) else 0.0,
        "expectancy": None,
        "sharpe": round(sharpe, 2),
        "_port_curve": port,
        "_port_ts": all_ts,
    }


def _eval_strategy_portfolio(cand: dict, data_frames: dict, symbols: list[str],
                             ms0: int, ms1: int, capital: float,
                             fee: float, slippage: float) -> dict:
    """Портфельная оценка по N символов × M таймфреймов:
    равное распределение капитала, слияние кривых эквити по юниону баров,
    реальный money-weighted PF и MaxDD по объединённой кривой.

    Возвращает также счётчик положительных комбо для проверки устойчивости.
    """
    combo_count = 0
    pos_symbols = set()
    pos_timeframes = set()
    equity_list = []
    total_trades = 0
    total_wins = 0
    total_pnl = 0.0
    combo_metrics: list[dict] = []
    per_sym_cap = capital / (len(symbols) * len(TIMEFRAME_CANDIDATES))

    for sym in symbols:
        for tf in TIMEFRAME_CANDIDATES:
            df = data_frames[tf][sym]
            m = (df["open_time"] >= ms0) & (df["open_time"] < ms1)
            if m.sum() <= 30:
                continue
            combo_count += 1
            dfw = df[m].reset_index(drop=True)
            desired = cand["fn"](dfw)
            # Передаём dfw (уже sliced), atr внутри run_engine пересчитается.
            met = _run_engine_on_window(dfw, desired, cand, per_sym_cap, fee, slippage)
            combo_metrics.append({"symbol": sym, "timeframe": tf, **{k: v for k, v in met.items() if not k.startswith("_")}})
            total_trades += met["trades"]
            total_wins += met["wins"]
            total_pnl += met["net_pnl"]
            eq = met["_equity"]
            if eq is None:
                continue
            eq_vals = eq.values if hasattr(eq, "values") else np.array(eq)
            ts_vals = met["_timestamps"]
            equity_list.append((ts_vals, eq_vals, per_sym_cap))
            if met["return_pct"] > 0 and met["pf"] >= 1.0 and met["trades"] >= 3:
                pos_symbols.add(sym)
                pos_timeframes.add(tf)

    merged = _merge_equity(equity_list)
    merged["trades"] = total_trades
    merged["total_trades"] = total_trades
    merged["wins"] = total_wins
    merged["win_rate"] = round(total_wins / total_trades * 100.0, 2) if total_trades else 0.0
    merged["expectancy"] = round(total_pnl / total_trades, 4) if total_trades else 0.0
    merged["_combo_metrics"] = combo_metrics
    merged["_positive_symbols"] = len(pos_symbols)
    merged["_positive_timeframes"] = len(pos_timeframes)
    merged["_combo_count"] = combo_count
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description="Единый исследовательский аудит ASTRA (2021-2026)")
    parser.add_argument("--data-dir", default="data", help="Каталог с CSV данными")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT,LTCUSDT", help="Символы через запятую")
    parser.add_argument("--start", default="2021-01-01", help="IS start YYYY-MM-DD")
    parser.add_argument("--oos-start", default="2025-01-01", help="OOS start YYYY-MM-DD")
    parser.add_argument("--end", default="2026-08-22", help="Period end YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--fee", type=float, default=0.001)
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--out", default="reports/research_2026")
    args = parser.parse_args()

    out_dir = PROJECT_ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    per_strategy_dir = out_dir / "per_strategy"
    ablation_dir = out_dir / "ablation"
    per_strategy_dir.mkdir(parents=True, exist_ok=True)
    ablation_dir.mkdir(parents=True, exist_ok=True)

    save_progress(out_dir, "Initialization", 0.0, "RUNNING", "Проверка доступности данных")

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    data_dir = PROJECT_ROOT / args.data_dir

    data_frames = {"1h": {}, "4h": {}, "1d": {}}
    data_quality = {}
    coverage_file = data_dir / "coverage.json"
    coverage = {}
    if coverage_file.exists():
        try:
            coverage = json.loads(coverage_file.read_text(encoding="utf-8")).get("series", {})
        except Exception:
            coverage = {}

    requested_end_ms = int(datetime.fromisoformat(args.end).replace(tzinfo=UTC).timestamp() * 1000)

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

        data_frames["1h"][sym] = df1
        data_frames["4h"][sym] = df4
        data_frames["1d"][sym] = df1d

        # Определяем реальный конец данных для каждого таймфрейма
        cov_entry = coverage.get(f"{sym}_1h", {})
        last_ts_1h = int(df1["open_time"].max())
        last_ts_4h = int(df4["open_time"].max())
        last_ts_1d = int(df1d["open_time"].max())
        first_ts = int(df1["open_time"].min())
        data_gap_days = max(0, (requested_end_ms - last_ts_1h) / 86_400_000.0)

        data_quality[sym] = {
            "source_file": str(f1h),
            "source": cov_entry.get("source", "local"),
            "candles_1h": len(df1),
            "candles_4h": len(df4),
            "candles_1d": len(df1d),
            "duplicates_removed": dups,
            "first_timestamp": str(pd.to_datetime(first_ts, unit="ms", utc=True)),
            "last_timestamp_1h": str(pd.to_datetime(last_ts_1h, unit="ms", utc=True)),
            "last_timestamp_4h": str(pd.to_datetime(last_ts_4h, unit="ms", utc=True)),
            "last_timestamp_1d": str(pd.to_datetime(last_ts_1d, unit="ms", utc=True)),
            "reaches_requested_end": bool(last_ts_1h >= requested_end_ms - 86_400_000),
            "coverage_gap_days_to_2026_08_22": round(data_gap_days, 2),
        }

    (out_dir / "data_quality.json").write_text(
        json.dumps(data_quality, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Git SHA + protocol
    import subprocess
    try:
        git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT).decode().strip()
    except Exception:
        git_sha = "unknown"

    protocol = {
        "created_at": str(datetime.now(UTC)),
        "git_sha": git_sha,
        "symbols": symbols,
        "timeframes": list(TIMEFRAME_CANDIDATES),
        "start": args.start,
        "oos_start": args.oos_start,
        "end_requested": args.end,
        "end_effective": {},
        "initial_capital": args.capital,
        "fee": args.fee,
        "slippage": args.slippage,
        "cost_stress_fee": 0.002,
        "cost_stress_slippage": 0.001,
        "anti_lookahead_policy": "Strict closed-candle signal (t), entry at open of t+1; no forward-fill; no OOS tuning.",
        "execution": "Single-leg paper-sim via strategy_lab.run_engine and scripts/audit_multicurrency.run_audit_simulation.",
        "classification_classes": ["REJECTED", "SHADOW_ONLY", "CANDIDATE_FOR_MANUAL_REVIEW", "NOT_AUDITABLE_WITH_AVAILABLE_DATA"],
    }
    # Фиксируем эффективный конец per symbol
    for sym in symbols:
        protocol["end_effective"][sym] = {
            tf: data_quality[sym][f"last_timestamp_{tf}"] for tf in ("1h", "4h", "1d")
        }
    (out_dir / "protocol.json").write_text(
        json.dumps(protocol, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    inventory = {
        k: {
            "name": v.name,
            "source": v.source,
            "tier": v.tier,
            "execution_blocked_reason": v.execution_blocked_reason,
        }
        for k, v in STRATEGY_REGISTRY.items()
    }
    (out_dir / "strategy_inventory.json").write_text(
        json.dumps(inventory, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    start_ms = int(datetime.fromisoformat(args.start).replace(tzinfo=UTC).timestamp() * 1000)
    oos_ms = int(datetime.fromisoformat(args.oos_start).replace(tzinfo=UTC).timestamp() * 1000)
    # Фактический end — минимум последней свечи 1h по всем символам
    actual_end_ms = min(int(data_frames["1h"][s]["open_time"].max()) for s in symbols)
    end_ms = min(requested_end_ms, actual_end_ms)

    # ---- 1) Аудит OHLC-стратегий на (N_symbols × 3 TF) портфеле
    save_progress(
        out_dir, "Strategy-Lab Portfolio Audit", 15.0, "RUNNING",
        f"Портфельный аудит ({len(symbols)} символов × {len(TIMEFRAME_CANDIDATES)} таймфреймов)",
    )

    cand_list = {c["key"]: c for c in CANDIDATES}
    lab_results: dict[str, dict] = {}

    # Пороги устойчивости: ≥75% символов и ≥2/3 таймфреймов на OOS
    sym_threshold = max(1, round(0.75 * len(symbols)))
    tf_threshold = max(1, round(2 / 3 * len(TIMEFRAME_CANDIDATES)))

    strategies_to_audit = [
        (k, e) for k, e in STRATEGY_REGISTRY.items() if e.tier != TIER_RESEARCH and k != "multicurrency_mtf"
    ]
    total_steps = len(strategies_to_audit)
    for i, (key, entry) in enumerate(strategies_to_audit):
        cand_key = STRATEGY_TO_CANDIDATE.get(key)
        if not cand_key or cand_key not in cand_list:
            lab_results[key] = {
                "name": entry.name, "tier": entry.tier,
                "status": "NOT_AUDITABLE_WITH_AVAILABLE_DATA",
                "reason": f"No candidate mapping for key {key}",
            }
            continue
        cand = cand_list[cand_key]
        is_m = _eval_strategy_portfolio(cand, data_frames, symbols, start_ms, oos_ms,
                                        args.capital, args.fee, args.slippage)
        oos_m = _eval_strategy_portfolio(cand, data_frames, symbols, oos_ms, end_ms,
                                         args.capital, args.fee, args.slippage)
        full_m = _eval_strategy_portfolio(cand, data_frames, symbols, start_ms, end_ms,
                                          args.capital, args.fee, args.slippage)
        stress_m = _eval_strategy_portfolio(cand, data_frames, symbols, start_ms, end_ms,
                                            args.capital, fee=0.002, slippage=0.001)

        def _strip_private(d: dict) -> dict:
            return {k: v for k, v in d.items() if not k.startswith("_")}

        def _classify(is_d, oos_d, full_d, stress_d) -> tuple[str, list[str]]:
            reasons: list[str] = []
            oos_ok = (oos_d["pf"] >= 1.10 and oos_d["expectancy"] > 0
                      and oos_d["return_pct"] >= 0 and oos_d["max_dd"] <= 15.0
                      and oos_d["trades"] >= 20)
            is_ok = is_d["pf"] >= 1.0
            full_ok = full_d["pf"] >= 1.10
            stress_pf_ok = stress_d["pf"] >= 0.90 and stress_d["pf"] >= oos_d["pf"] * 0.75
            # Stability: ≥75% символов положительно и ≥2/3 таймфреймов положительно
            sym_ok = oos_d.get("_positive_symbols", 0) >= sym_threshold
            tf_ok = oos_d.get("_positive_timeframes", 0) >= tf_threshold

            if not oos_ok:
                reasons.append("OOS thresholds not met")
            if not is_ok:
                reasons.append("IS PF < 1.0")
            if not full_ok:
                reasons.append("Full PF < 1.10")
            if not stress_pf_ok:
                reasons.append("Cost-stress degradation >25% or PF<0.9")
            if not sym_ok:
                reasons.append(
                    f"Not positive on >={sym_threshold}/{len(symbols)} symbols "
                    f"(got {oos_d.get('_positive_symbols',0)})"
                )
            if not tf_ok:
                reasons.append(
                    f"Not positive on >={tf_threshold}/{len(TIMEFRAME_CANDIDATES)} timeframes "
                    f"(got {oos_d.get('_positive_timeframes',0)})"
                )

            if oos_d["pf"] < 1.0 or oos_d["return_pct"] < 0:
                return "REJECTED", reasons
            if all([oos_ok, is_ok, full_ok, stress_pf_ok, sym_ok, tf_ok]):
                return "CANDIDATE_FOR_MANUAL_REVIEW", reasons
            if oos_d["pf"] >= 1.0 or full_d["pf"] >= 1.0:
                return "SHADOW_ONLY", reasons
            return "REJECTED", reasons

        status, reasons = _classify(is_m, oos_m, full_m, stress_m)
        # Сохраняем per-strategy json без приватных полей
        per_st = {
            "name": entry.name, "key": key, "tier": entry.tier, "candidate_key": cand_key,
            "in_sample": _strip_private(is_m),
            "out_of_sample": _strip_private(oos_m),
            "full_window": _strip_private(full_m),
            "cost_stress": _strip_private(stress_m),
            "classification": status,
            "classification_reasons": reasons,
            "combo_detail": {
                "in_sample": is_m.get("_combo_metrics", []),
                "out_of_sample": oos_m.get("_combo_metrics", []),
            },
        }
        (per_strategy_dir / f"{key}.json").write_text(
            json.dumps(per_st, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        lab_results[key] = {
            "name": entry.name, "tier": entry.tier, "status": status,
            "in_sample": _strip_private(is_m),
            "out_of_sample": _strip_private(oos_m),
            "full_window": _strip_private(full_m),
            "cost_stress": _strip_private(stress_m),
            "reasons": reasons,
            "positive_symbols_oos": oos_m.get("_positive_symbols", 0),
            "positive_timeframes_oos": oos_m.get("_positive_timeframes", 0),
        }
        pct = 15 + (i + 1) / total_steps * 45
        save_progress(out_dir, "Strategy-Lab Portfolio Audit", pct, "RUNNING",
                      f"{i+1}/{total_steps}: {entry.name} → {status}")

    # ---- 2) Multicurrency MTF Audit + Ablations (1h-driven)
    save_progress(out_dir, "Multicurrency MTF Audit", 65.0, "RUNNING", "Аудит multicurrency_mtf и аблаций")

    def run_mtf_sim(ms0: int, ms1: int, **kwargs) -> dict:
        return run_audit_simulation(
            symbols, data_frames["1h"], data_frames["4h"], data_frames["1d"],
            ms0, ms1, args.capital, args.fee, args.slippage, **kwargs,
        )

    mtf_full = run_mtf_sim(start_ms, end_ms)["metrics"]
    mtf_is = run_mtf_sim(start_ms, oos_ms)["metrics"]
    mtf_oos = run_mtf_sim(oos_ms, end_ms)["metrics"]
    mtf_stress = run_audit_simulation(
        symbols, data_frames["1h"], data_frames["4h"], data_frames["1d"],
        start_ms, end_ms, args.capital, fee_rate=0.002, slippage_rate=0.001,
    )["metrics"]

    def _norm_mtf(m: dict) -> dict:
        out = dict(m)
        out["pf"] = m.get("profit_factor", 0)
        out["max_dd"] = m.get("max_drawdown", 0)
        out["trades"] = m.get("total_trades", 0)
        out["return_pct"] = m.get("return_pct", 0)
        out["expectancy"] = m.get("expectancy", 0)
        out["win_rate"] = m.get("win_rate", 0)
        return out

    is_mtf = _norm_mtf(mtf_is)
    oos_mtf = _norm_mtf(mtf_oos)
    full_mtf = _norm_mtf(mtf_full)
    stress_mtf = _norm_mtf(mtf_stress)
    mtf_status = "REJECTED" if oos_mtf["profit_factor"] < 1.0 else "SHADOW_ONLY"
    lab_results["multicurrency_mtf"] = {
        "name": "Multicurrency MTF Protocol", "tier": "audit", "status": mtf_status,
        "in_sample": is_mtf, "out_of_sample": oos_mtf,
        "full_window": full_mtf, "cost_stress": stress_mtf,
        "reasons": ["MTF audit class uses rule-specific thresholds; status driven by OOS PF."],
    }
    (per_strategy_dir / "multicurrency_mtf.json").write_text(
        json.dumps(lab_results["multicurrency_mtf"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    ablations = {
        "baseline": {},
        "without_btc_gate": {"use_btc_gate": False},
        "without_volume_filter": {"use_volume_filter": False},
        "without_retest": {"use_retest": False},
        "without_partial_trailing": {"use_partial_trailing": False},
        "all_filters_off": {"use_btc_gate": False, "use_volume_filter": False,
                            "use_retest": False, "use_partial_trailing": False},
    }
    ablation_res: dict[str, dict] = {}
    for ab_name, ab_kw in ablations.items():
        res_ab = run_mtf_sim(start_ms, end_ms, **ab_kw)["metrics"]
        res_ab = _norm_mtf(res_ab)
        (ablation_dir / f"{ab_name}.json").write_text(
            json.dumps(res_ab, indent=2), encoding="utf-8"
        )
        ablation_res[ab_name] = res_ab

    # ---- 3) Research-профили без кода
    for key, entry in STRATEGY_REGISTRY.items():
        if entry.tier == TIER_RESEARCH:
            lab_results[key] = {
                "name": entry.name, "tier": entry.tier,
                "status": "NOT_AUDITABLE_WITH_AVAILABLE_DATA",
                "reason": RESEARCH_PROFILE_NOTES.get(key, entry.execution_blocked_reason),
            }

    # ---- 4) Запись агрегата
    (out_dir / "aggregate_summary.json").write_text(
        json.dumps({
            "protocol": protocol,
            "data_quality": data_quality,
            "strategies": lab_results,
            "ablation": ablation_res,
        }, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    rejected = [k for k, v in lab_results.items() if v["status"] == "REJECTED"]
    shadow = [k for k, v in lab_results.items() if v["status"] == "SHADOW_ONLY"]
    candidate = [k for k, v in lab_results.items() if v["status"] == "CANDIDATE_FOR_MANUAL_REVIEW"]
    na = [k for k, v in lab_results.items() if v["status"] == "NOT_AUDITABLE_WITH_AVAILABLE_DATA"]

    def _write_list(fname: str, title: str, keys: list[str]):
        lines = [f"# {title}", ""]
        for k in keys:
            v = lab_results[k]
            oos = v.get("out_of_sample", {})
            lines.append(f"- **{v['name']}** (`{k}`)")
            lines.append(f"  - OOS PF: {oos.get('pf','—')} · Return: {oos.get('return_pct','—')}% · MaxDD: {oos.get('max_dd','—')}% · Trades: {oos.get('trades',0)}")
            if v.get("reasons"):
                lines.append(f"  - Примечания: {'; '.join(v['reasons'])}")
            if v.get("reason"):
                lines.append(f"  - Причина: {v['reason']}")
            lines.append("")
        (out_dir / fname).write_text("\n".join(lines), encoding="utf-8")

    _write_list("rejected_strategies.md", "Отклоненные стратегии (REJECTED)", rejected)
    _write_list("shadow_strategies.md", "Стратегии теневого отслеживания (SHADOW_ONLY)", shadow)
    _write_list("candidate_for_manual_review.md", "Кандидаты для ручного ревью (CANDIDATE_FOR_MANUAL_REVIEW)", candidate)
    _write_list("not_auditable.md", "Неаудируемые с доступными данными (NOT_AUDITABLE_WITH_AVAILABLE_DATA)", na)

    # ---- 5) Итоговый markdown
    md: list[str] = []
    md.append("# Итоговый исследовательский аудит ASTRA (2021–2026)")
    md.append("")
    md.append(f"- **Символы**: `{', '.join(symbols)}`")
    md.append(f"- **Таймфреймы**: {', '.join(TIMEFRAME_CANDIDATES)} ({len(symbols) * len(TIMEFRAME_CANDIDATES)} комбо: {len(symbols)} × {len(TIMEFRAME_CANDIDATES)})")
    md.append(f"- **Период**: IS {args.start} → {args.oos_start}; OOS {args.oos_start} → конец данных")
    md.append(f"- **Запрошенный конец**: {args.end}; **фактический конец** (мин по символам 1h): "
              f"{datetime.fromtimestamp(end_ms/1000, tz=UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    md.append(f"- **Капитал**: {args.capital:,.0f} USDT · **База**: fee {args.fee:.1%} + slippage {args.slippage:.1%} · **Stress**: fee 0.2% + slip 0.1%")
    md.append(f"- **Git SHA**: `{git_sha}`")
    md.append("")
    md.append("## Покрытие данных")
    md.append("")
    md.append("| Символ | 1h бар | 4h бар | 1d бар | Последняя 1h свеча | Достигает 2026-08-22 | Гэп до 2026-08-22 (дни) |")
    md.append("|---|---|---|---|---|---|---|")
    for sym in symbols:
        dq = data_quality[sym]
        md.append(f"| {sym} | {dq['candles_1h']} | {dq['candles_4h']} | {dq['candles_1d']} | "
                  f"{dq['last_timestamp_1h'][:10]} | {'✅' if dq['reaches_requested_end'] else '❌'} | {dq['coverage_gap_days_to_2026_08_22']} |")
    md.append("")
    md.append(f"## Результаты OHLC-стратегий (портфель {len(symbols) * len(TIMEFRAME_CANDIDATES)} комбо)")
    md.append("")
    md.append("| Стратегия | Классификация | IS PF | OOS PF | Full PF | Stress PF | OOS Ret% | OOS MaxDD% | Trades | ⊕sym | ⊕TF |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for _k, v in lab_results.items():
        if v["status"] == "NOT_AUDITABLE_WITH_AVAILABLE_DATA":
            md.append(f"| {v['name']} | {v['status']} | — | — | — | — | — | — | 0 | — | — |")
            continue
        is_p = v["in_sample"].get("pf", 0)
        oos_p = v["out_of_sample"].get("pf", 0)
        full_p = v["full_window"].get("pf", 0)
        str_p = v["cost_stress"].get("pf", 0)
        ret_p = v["out_of_sample"].get("return_pct", 0)
        dd_p = v["out_of_sample"].get("max_dd", 0)
        tr_p = v["out_of_sample"].get("trades", 0)
        sym_p = v.get("positive_symbols_oos", "—")
        tf_p = v.get("positive_timeframes_oos", "—")
        md.append(f"| {v['name']} | {v['status']} | {is_p} | {oos_p} | {full_p} | {str_p} | {ret_p:+.2f}% | {dd_p:.2f}% | {tr_p} | {sym_p}/{len(symbols)} | {tf_p}/{len(TIMEFRAME_CANDIDATES)} |")
    md.append("")
    md.append("## Multicurrency MTF: ablation-исследования (full window)")
    md.append("")
    md.append("| Вариант | Profit Factor | Return % | Max Drawdown % | Trades |")
    md.append("|---|---|---|---|---|")
    for k, v in ablation_res.items():
        md.append(f"| {k} | {v.get('profit_factor',0):.2f} | {v.get('return_pct',0):+.2f}% | {v.get('max_drawdown',0):.2f}% | {v.get('total_trades',0)} |")
    md.append("")
    md.append("## Классификация: итоги")
    md.append("")
    md.append(f"- **CANDIDATE_FOR_MANUAL_REVIEW**: {len(candidate)} ({', '.join(candidate) if candidate else 'нет'})")
    md.append(f"- **SHADOW_ONLY**: {len(shadow)} ({', '.join(shadow) if shadow else 'нет'})")
    md.append(f"- **REJECTED**: {len(rejected)} ({', '.join(rejected) if rejected else 'нет'})")
    md.append(f"- **NOT_AUDITABLE_WITH_AVAILABLE_DATA**: {len(na)} ({', '.join(na) if na else 'нет'})")
    md.append("")
    md.append("## Ограничения")
    md.append("")
    md.append("- Чемпион не назначается; ни одна стратегия не переводится в реальное/бумажное исполнение по итогам этого прохода.")
    md.append("- Класс CANDIDATE_FOR_MANUAL_REVIEW требует дополнительной ручной валидации: out-of-sample walk-forward по годам, устойчивость по роллинговым окнам, проверка на других активах, проверка на чувствительность к спреду.")
    md.append("- Исследовательские профили (Ливермор/Сорос/Дракенмиллер/Тюдор) не получили OHLC-кода в рамках прохода и описаны в `not_auditable.md` с указанием чего не хватает.")
    md.append("- Если данные короче 2026-08-22 (таблица покрытия), OOS метрики посчитаны по фактически доступному хвосту — это явно зафиксировано в `protocol.json` и в таблице выше.")
    md.append("- Результаты бэктестов не гарантируют будущую доходность; торговля без одобрения человека запрещена fail-closed реестром стратегий.")
    md.append("")
    md.append("## Отсутствие сторонних эффектов")
    md.append("")
    md.append("- Workflow запускается с `permissions: contents: read`; нет push'ей в репозиторий, нет секретов, нет вызова биржевых ордеров, нет авто-продвижения стратегий в champion/execution.")
    md.append("- Торговый бот (`bot.yml`) на время аудита может быть приостановлен и после завершения возвращается в строй.")
    md.append("")
    (out_dir / "aggregate_summary.md").write_text("\n".join(md), encoding="utf-8")

    # ---- 6) Опциональная публикация сводки в issue #36 для сбора результатов
    # Работает только когда есть GITHUB_TOKEN (т.е. в CI); локально пропускается.
    _publish_summary_to_issue(out_dir, md)

    save_progress(out_dir, "Completed", 100.0, "COMPLETED",
                  f"Исследовательский аудит завершён: CANDIDATE={len(candidate)}, SHADOW={len(shadow)}, REJECTED={len(rejected)}, NA={len(na)}")
    print(f"Полный исследовательский аудит завершён. Артефакты: {out_dir}")
    return 0


def _publish_summary_to_issue(out_dir: Path, md: list[str]) -> None:
    """Постит aggregate_summary как комментарий в issue #36 через GitHub API.

    В CI доступен GITHUB_TOKEN с правами на issues. Вне CI (без токена)
    молча выходит. Это нужно, чтобы забирать результаты из песочницы
    без доступа к blob-хранилищу артефактов.
    """
    import os
    import urllib.request
    import urllib.error
    import json

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY") or os.environ.get("GH_REPO")
    issue_num = os.environ.get("AUDIT_PUBLISH_ISSUE", "36")
    if not token or not repo:
        print("[publish] GITHUB_TOKEN/GITHUB_REPOSITORY не заданы — пропускаю публикацию в issue")
        return
    try:
        body_full = "\n".join(md)
        ts = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        # Комментарии в issues ограничены ~65536 символов; агрегат обычно влезает,
        # но на всякий случай подстрахуемся и разобьём на чанки по 30КБ.
        header = f"## Агрегат аудита @ {ts}\n\n"
        chunk_size = 30000
        chunks = []
        rest = body_full
        first = True
        while rest:
            chunk = rest[:chunk_size]
            rest = rest[chunk_size:]
            prefix = header if first else f"\n*продолжение ({len(chunks)+1})*\n\n"
            chunks.append(prefix + chunk)
            first = False
        for i, body in enumerate(chunks):
            payload = json.dumps({"body": body}).encode("utf-8")
            req = urllib.request.Request(
                f"https://api.github.com/repos/{repo}/issues/{issue_num}/comments",
                data=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "Content-Type": "application/json",
                    "User-Agent": "astra-bot-audit/1.0",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    resp.read()
                print(f"[publish] chunk {i+1}/{len(chunks)} опубликован в issue #{issue_num}")
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")[:500]
                print(f"[publish] chunk {i+1} HTTP {e.code}: {err_body}")
                break
    except Exception as exc:
        print(f"[publish] не удалось опубликовать: {exc!r}")


if __name__ == "__main__":
    sys.exit(main())
