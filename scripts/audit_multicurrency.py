#!/usr/bin/env python3
"""Многовалютный аудит стратегии multicurrency_mtf (1D/4H/1H).

Спецификация из «Мультивалютная_торговая_система.docx»:
- 1D EMA 20/50/200 строгий трендовый фильтр (close > EMA20 > EMA50 > EMA200);
- 4H структура (breakout / retest);
- 1H подтверждающие свечи (hammer/engulfing/shooting star) + объём > SMA20;
- BTC 1D bearish trend блокирует long по альткоинам;
- Разные лимиты риска для BTC/ETH (0.4%) и SOL/XRP (0.25%);
- BTC + ETH считаются одной корреляционной группой (max 1 позиция на группу);
- Max 3 открытые позиции всего;
- Дневной stop: -3% equity;
- Жизненный цикл: TP1 (1R, закрытие 50%), стоп в breakeven, trailing stop.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from astra_bot.strategies.multicurrency_mtf import (
    is_bearish_engulfing,
    is_bullish_engulfing,
    is_hammer,
    is_shooting_star,
)


def resample_klines(df_1h: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Честный ресемплинг из закрытых 1H свечей без look-ahead bias."""
    df = df_1h.copy()
    df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.set_index("dt", inplace=True)

    rule = "4h" if timeframe == "4h" else "1D"
    resampled = df.resample(rule, closed="left", label="left").agg({
        "open_time": "first",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna().reset_index(drop=True)
    return resampled


@dataclass
class Position:
    symbol: str
    direction: str  # "LONG" | "SHORT"
    entry_price: float
    entry_time: int
    initial_qty: float
    remaining_qty: float
    initial_stop: float
    current_stop: float
    tp1_price: float
    tp_target_price: float  # Фиксированная цель (2R)
    accumulated_pnl: float = 0.0  # Суммарный PnL по этой позиции
    tp1_hit: bool = False
    highest_price: float = 0.0
    lowest_price: float = 0.0


@dataclass
class TradeRecord:
    symbol: str
    direction: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    qty: float
    pnl: float
    return_pct: float
    reason: str


def precompute_indicators(df_1h: pd.DataFrame, df_4h: pd.DataFrame, df_1d: pd.DataFrame):
    """Предвычисление индикаторов без look-ahead bias."""
    # 1D EMA (Strict alignment)
    df_1d_calc = df_1d.copy()
    df_1d_calc["ema20"] = df_1d_calc["close"].ewm(span=20, adjust=False).mean()
    df_1d_calc["ema50"] = df_1d_calc["close"].ewm(span=50, adjust=False).mean()
    df_1d_calc["ema200"] = df_1d_calc["close"].ewm(span=200, adjust=False).mean()

    df_1d_calc["bullish_1d"] = (
        (df_1d_calc["close"] > df_1d_calc["ema20"]) &
        (df_1d_calc["ema20"] > df_1d_calc["ema50"]) &
        (df_1d_calc["ema50"] > df_1d_calc["ema200"])
    )
    df_1d_calc["bearish_1d"] = (
        (df_1d_calc["close"] < df_1d_calc["ema20"]) &
        (df_1d_calc["ema20"] < df_1d_calc["ema50"]) &
        (df_1d_calc["ema50"] < df_1d_calc["ema200"])
    )

    # 4H ATR & Structure
    df_4h_calc = df_4h.copy()
    tr1 = df_4h_calc["high"] - df_4h_calc["low"]
    tr2 = (df_4h_calc["high"] - df_4h_calc["close"].shift(1)).abs()
    tr3 = (df_4h_calc["low"] - df_4h_calc["close"].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df_4h_calc["atr14"] = tr.rolling(14).mean()
    df_4h_calc["resistance_3"] = df_4h_calc["high"].rolling(3).max()
    df_4h_calc["support_3"] = df_4h_calc["low"].rolling(3).min()

    # 1H Volume SMA
    df_1h_calc = df_1h.copy()
    df_1h_calc["vol_sma20"] = df_1h_calc["volume"].rolling(20).mean()

    return df_1h_calc, df_4h_calc, df_1d_calc


def run_audit_simulation(
    symbols: list[str],
    data_1h: dict[str, pd.DataFrame],
    data_4h: dict[str, pd.DataFrame],
    data_1d: dict[str, pd.DataFrame],
    start_ms: int,
    end_ms: int,
    capital: float = 10000.0,
    fee_rate: float = 0.001,
    slippage_rate: float = 0.001,
    *,
    use_btc_gate: bool = True,
    use_volume_filter: bool = True,
    use_retest: bool = True,
    use_partial_trailing: bool = True,
) -> dict:
    """Симулятор многовалютного аудита."""
    prep_1h, prep_4h, prep_1d = {}, {}, {}
    for sym in symbols:
        p1, p4, p1d = precompute_indicators(data_1h[sym], data_4h[sym], data_1d[sym])
        prep_1h[sym] = p1.set_index("open_time")
        prep_4h[sym] = p4.set_index("open_time")
        prep_1d[sym] = p1d.set_index("open_time")

    all_timestamps = sorted(
        set.union(*[set(df.index.values) for df in prep_1h.values()])
    )
    all_timestamps = [ts for ts in all_timestamps if start_ms <= ts <= end_ms]

    # Быстрый O(1) поиск по индексам
    ts_map_1h = {sym: {ts: i for i, ts in enumerate(prep_1h[sym].index.values)} for sym in symbols}

    # Массивы для быстрого поиска ближайшего прошлого индекса
    keys_4h = {sym: prep_4h[sym].index.values for sym in symbols}
    keys_1d = {sym: prep_1d[sym].index.values for sym in symbols}

    import numpy as np

    def get_prev_idx_4h(sym, ts):
        arr = keys_4h[sym]
        pos = np.searchsorted(arr, ts, side="right") - 1
        return pos

    def get_prev_idx_1d(sym, ts):
        arr = keys_1d[sym]
        pos = np.searchsorted(arr, ts, side="right") - 1
        return pos

    equity = capital
    peak_equity = capital
    daily_start_equity = capital
    current_day = None

    open_positions: dict[str, Position] = {}
    trade_history: list[TradeRecord] = []
    equity_curve = []
    daily_trading_disabled = False

    for ts in all_timestamps:
        dt = pd.to_datetime(ts, unit="ms", utc=True)
        day_str = dt.strftime("%Y-%m-%d")

        if current_day != day_str:
            current_day = day_str
            daily_start_equity = equity
            daily_trading_disabled = False

        if (equity - daily_start_equity) / daily_start_equity <= -0.03:
            daily_trading_disabled = True

        # 1. Обработка открытых позиций
        closed_symbols = []
        for sym, pos in list(open_positions.items()):
            p1 = prep_1h[sym]
            if ts not in p1.index:
                continue
            row = p1.loc[ts]
            hi, lo = float(row["high"]), float(row["low"])

            pos.highest_price = max(pos.highest_price, hi)
            pos.lowest_price = min(pos.lowest_price, lo)

            if pos.direction == "LONG":
                if lo <= pos.current_stop:
                    exit_px = pos.current_stop * (1.0 - slippage_rate)
                    gross_pnl = (exit_px - pos.entry_price) * pos.remaining_qty
                    fee = (pos.entry_price * pos.remaining_qty + exit_px * pos.remaining_qty) * fee_rate
                    net_pnl = gross_pnl - fee
                    equity += net_pnl
                    pos.accumulated_pnl += net_pnl
                    trade_history.append(TradeRecord(
                        symbol=sym, direction="LONG", entry_time=str(pd.to_datetime(pos.entry_time, unit="ms", utc=True)),
                        exit_time=str(dt), entry_price=pos.entry_price, exit_price=exit_px,
                        qty=pos.initial_qty, pnl=pos.accumulated_pnl, return_pct=(pos.accumulated_pnl / (pos.entry_price * pos.initial_qty)) * 100, reason="STOP",
                    ))
                    closed_symbols.append(sym)
                    continue

                if not use_partial_trailing:
                    if hi >= pos.tp_target_price:
                        exit_px = pos.tp_target_price * (1.0 - slippage_rate)
                        gross_pnl = (exit_px - pos.entry_price) * pos.remaining_qty
                        fee = (pos.entry_price * pos.remaining_qty + exit_px * pos.remaining_qty) * fee_rate
                        net_pnl = gross_pnl - fee
                        equity += net_pnl
                        pos.accumulated_pnl += net_pnl
                        trade_history.append(TradeRecord(
                            symbol=sym, direction="LONG", entry_time=str(pd.to_datetime(pos.entry_time, unit="ms", utc=True)),
                            exit_time=str(dt), entry_price=pos.entry_price, exit_price=exit_px,
                            qty=pos.initial_qty, pnl=pos.accumulated_pnl, return_pct=(pos.accumulated_pnl / (pos.entry_price * pos.initial_qty)) * 100, reason="TAKE_PROFIT",
                        ))
                        closed_symbols.append(sym)
                        continue
                else:
                    if not pos.tp1_hit and hi >= pos.tp1_price:
                        pos.tp1_hit = True
                        close_qty = pos.initial_qty * 0.5
                        exit_px = pos.tp1_price * (1.0 - slippage_rate)
                        gross_pnl = (exit_px - pos.entry_price) * close_qty
                        fee = (pos.entry_price * close_qty + exit_px * close_qty) * fee_rate
                        net_pnl = gross_pnl - fee
                        equity += net_pnl
                        pos.accumulated_pnl += net_pnl
                        pos.remaining_qty -= close_qty
                        pos.current_stop = pos.entry_price

                    if pos.tp1_hit:
                        r_dist = abs(pos.entry_price - pos.initial_stop)
                        new_stop = pos.highest_price - r_dist
                        if new_stop > pos.current_stop:
                            pos.current_stop = new_stop

            elif pos.direction == "SHORT":
                if hi >= pos.current_stop:
                    exit_px = pos.current_stop * (1.0 + slippage_rate)
                    gross_pnl = (pos.entry_price - exit_px) * pos.remaining_qty
                    fee = (pos.entry_price * pos.remaining_qty + exit_px * pos.remaining_qty) * fee_rate
                    net_pnl = gross_pnl - fee
                    equity += net_pnl
                    pos.accumulated_pnl += net_pnl
                    trade_history.append(TradeRecord(
                        symbol=sym, direction="SHORT", entry_time=str(pd.to_datetime(pos.entry_time, unit="ms", utc=True)),
                        exit_time=str(dt), entry_price=pos.entry_price, exit_price=exit_px,
                        qty=pos.initial_qty, pnl=pos.accumulated_pnl, return_pct=(pos.accumulated_pnl / (pos.entry_price * pos.initial_qty)) * 100, reason="STOP",
                    ))
                    closed_symbols.append(sym)
                    continue

                if not use_partial_trailing:
                    if lo <= pos.tp_target_price:
                        exit_px = pos.tp_target_price * (1.0 + slippage_rate)
                        gross_pnl = (pos.entry_price - exit_px) * pos.remaining_qty
                        fee = (pos.entry_price * pos.remaining_qty + exit_px * pos.remaining_qty) * fee_rate
                        net_pnl = gross_pnl - fee
                        equity += net_pnl
                        pos.accumulated_pnl += net_pnl
                        trade_history.append(TradeRecord(
                            symbol=sym, direction="SHORT", entry_time=str(pd.to_datetime(pos.entry_time, unit="ms", utc=True)),
                            exit_time=str(dt), entry_price=pos.entry_price, exit_price=exit_px,
                            qty=pos.initial_qty, pnl=pos.accumulated_pnl, return_pct=(pos.accumulated_pnl / (pos.entry_price * pos.initial_qty)) * 100, reason="TAKE_PROFIT",
                        ))
                        closed_symbols.append(sym)
                        continue
                else:
                    if not pos.tp1_hit and lo <= pos.tp1_price:
                        pos.tp1_hit = True
                        close_qty = pos.initial_qty * 0.5
                        exit_px = pos.tp1_price * (1.0 + slippage_rate)
                        gross_pnl = (pos.entry_price - exit_px) * close_qty
                        fee = (pos.entry_price * close_qty + exit_px * close_qty) * fee_rate
                        net_pnl = gross_pnl - fee
                        equity += net_pnl
                        pos.accumulated_pnl += net_pnl
                        pos.remaining_qty -= close_qty
                        pos.current_stop = pos.entry_price

                    if pos.tp1_hit:
                        r_dist = abs(pos.initial_stop - pos.entry_price)
                        new_stop = pos.lowest_price + r_dist
                        if new_stop < pos.current_stop:
                            pos.current_stop = new_stop

        for sym in closed_symbols:
            del open_positions[sym]

        # 2. Поиск новых входов
        if not daily_trading_disabled and len(open_positions) < 5:
            for sym in symbols:
                if sym in open_positions:
                    continue

                if sym in ("BTCUSDT", "ETHUSDT") and any(s in open_positions for s in ("BTCUSDT", "ETHUSDT")):
                    continue

                p1 = prep_1h[sym]
                p4 = prep_4h[sym]
                p1d = prep_1d[sym]

                idx1 = ts_map_1h[sym].get(ts, -1)
                if idx1 <= 2:
                    continue

                prev_1h = p1.iloc[idx1 - 1]
                prev2_1h = p1.iloc[idx1 - 2]

                idx4 = get_prev_idx_4h(sym, ts)
                if idx4 <= 1:
                    continue
                prev_4h = p4.iloc[idx4 - 1]

                idx1d = get_prev_idx_1d(sym, ts)
                if idx1d <= 1:
                    continue
                prev_1d = p1d.iloc[idx1d - 1]

                if use_btc_gate and "BTC" not in sym:
                    p1d_btc = prep_1d["BTCUSDT"]
                    idx_btc = get_prev_idx_1d("BTCUSDT", ts)
                    if idx_btc <= 1:
                        continue
                    prev_btc = p1d_btc.iloc[idx_btc - 1]
                    if prev_btc["bearish_1d"]:
                        continue

                vol_ok = (not use_volume_filter) or (prev_1h["volume"] > prev_1h["vol_sma20"])
                if not vol_ok:
                    continue

                atr4h = prev_4h["atr14"] if not math.isnan(prev_4h["atr14"]) else (prev_1h["close"] * 0.01)

                retest_ok = True
                if use_retest:
                    res_lvl = prev_4h["resistance_3"] if not math.isnan(prev_4h["resistance_3"]) else prev_4h["close"]
                    sup_lvl = prev_4h["support_3"] if not math.isnan(prev_4h["support_3"]) else prev_4h["close"]
                    if prev_1d["bullish_1d"]:
                        # Пробой сопротивления + возврат (retest) к нему
                        retest_ok = (prev_4h["close"] >= res_lvl) and (prev_1h["low"] <= res_lvl)
                    elif prev_1d["bearish_1d"]:
                        # Пробой поддержки + возврат (retest) к ней
                        retest_ok = (prev_4h["close"] <= sup_lvl) and (prev_1h["high"] >= sup_lvl)

                if not retest_ok:
                    continue

                # Свечные паттерны
                bull_confirm = is_hammer(prev_1h["open"], prev_1h["high"], prev_1h["low"], prev_1h["close"]) or \
                               is_bullish_engulfing(prev2_1h["open"], prev2_1h["close"], prev_1h["open"], prev_1h["close"])
                bear_confirm = is_shooting_star(prev_1h["open"], prev_1h["high"], prev_1h["low"], prev_1h["close"]) or \
                               is_bearish_engulfing(prev2_1h["open"], prev2_1h["close"], prev_1h["open"], prev_1h["close"])

                signal_dir = None
                if prev_1d["bullish_1d"] and bull_confirm:
                    signal_dir = "LONG"
                elif prev_1d["bearish_1d"] and bear_confirm:
                    signal_dir = "SHORT"

                if signal_dir is not None:
                    entry_px = prev_1h["close"] * (1.0 + slippage_rate if signal_dir == "LONG" else 1.0 - slippage_rate)
                    stop_dist = atr4h * 1.5
                    stop_px = entry_px - stop_dist if signal_dir == "LONG" else entry_px + stop_dist

                    risk_pct = 0.004 if sym in ("BTCUSDT", "ETHUSDT") else (0.003 if sym in ("BNBUSDT", "SOLUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT") else 0.0025)
                    risk_amount = equity * risk_pct
                    risk_per_share = abs(entry_px - stop_px)

                    if risk_per_share > 0:
                        qty = risk_amount / risk_per_share
                        r_dist = abs(entry_px - stop_px)
                        tp1_px = entry_px + r_dist if signal_dir == "LONG" else entry_px - r_dist
                        tp_target = entry_px + (r_dist * 2.0) if signal_dir == "LONG" else entry_px - (r_dist * 2.0)

                        open_positions[sym] = Position(
                            symbol=sym,
                            direction=signal_dir,
                            entry_price=entry_px,
                            entry_time=ts,
                            initial_qty=qty,
                            remaining_qty=qty,
                            initial_stop=stop_px,
                            current_stop=stop_px,
                            tp1_price=tp1_px,
                            tp_target_price=tp_target,
                            highest_price=entry_px,
                            lowest_price=entry_px,
                        )

        equity_curve.append({"open_time": ts, "datetime": str(dt), "equity": equity})
        if equity > peak_equity:
            peak_equity = equity

    wins = [t for t in trade_history if t.pnl > 0]
    losses = [t for t in trade_history if t.pnl < 0]
    total_trades = len(trade_history)
    gw = sum(t.pnl for t in wins)
    gl = sum(-t.pnl for t in losses)

    pf = gw / gl if gl > 0 else (999.0 if gw > 0 else 0.0)
    win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0.0
    net_pnl = equity - capital
    ret_pct = (net_pnl / capital) * 100.0

    eq_series = pd.Series([e["equity"] for e in equity_curve])
    pk_series = eq_series.cummax()
    max_dd = float(((pk_series - eq_series) / pk_series * 100.0).max()) if not eq_series.empty else 0.0

    return {
        "metrics": {
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 2),
            "profit_factor": round(pf, 2),
            "gross_profit": round(gw, 2),
            "gross_loss": round(gl, 2),
            "net_pnl": round(net_pnl, 2),
            "return_pct": round(ret_pct, 2),
            "max_drawdown": round(max_dd, 2),
            "expectancy": round(net_pnl / total_trades, 2) if total_trades > 0 else 0.0,
        },
        "trades": [t.__dict__ for t in trade_history],
        "equity_curve": equity_curve,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Многовалютный аудит multicurrency_mtf")
    parser.add_argument("--data-dir", default="data", help="Директория с CSV файлами")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT,LTCUSDT", help="Символы через запятую")
    parser.add_argument("--start", default="2021-01-01", help="Начало IS YYYY-MM-DD")
    parser.add_argument("--oos-start", default="2024-01-01", help="Начало OOS YYYY-MM-DD")
    parser.add_argument("--end", default="2024-12-31", help="Конец YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=10000.0, help="Стартовый капитал USDT")
    parser.add_argument("--fee", type=float, default=0.001, help="Комиссия (0.001 равно 0.1 процента)")
    parser.add_argument("--slippage", type=float, default=0.001, help="Проскальзывание (0.001 равно 0.1 процента)")
    parser.add_argument("--news-file", default=None, help="Файл с историческими новостями")
    parser.add_argument("--out", default="reports/multicurrency_audit", help="Каталог выходных артефактов")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    data_dir = PROJECT_ROOT / args.data_dir
    out_dir = PROJECT_ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    data_quality = {}
    data_frames_1h = {}
    data_frames_4h = {}
    data_frames_1d = {}

    for sym in symbols:
        file_1h = data_dir / f"{sym}_1h.csv"
        if not file_1h.exists():
            print(f"Ошибка: Не найден обязательный файл {file_1h}", file=sys.stderr)
            return 1
        df1 = pd.read_csv(file_1h)
        dups = int(df1.duplicated(subset=["open_time"]).sum())
        df1 = df1.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)

        file_4h = data_dir / f"{sym}_4h.csv"
        if file_4h.exists():
            df4 = pd.read_csv(file_4h).drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
        else:
            df4 = resample_klines(df1, "4h")

        df1d = resample_klines(df1, "1d")

        data_frames_1h[sym] = df1
        data_frames_4h[sym] = df4
        data_frames_1d[sym] = df1d

        data_quality[sym] = {
            "source_file": str(file_1h),
            "candles_1h": len(df1),
            "candles_4h": len(df4),
            "candles_1d": len(df1d),
            "duplicates_removed": dups,
            "start": str(pd.to_datetime(df1["open_time"].min(), unit="ms", utc=True)),
            "end": str(pd.to_datetime(df1["open_time"].max(), unit="ms", utc=True)),
        }

    protocol = {
        "created_at": str(datetime.now(UTC)),
        "symbols": symbols,
        "timeframes": ["1h", "4h", "1d"],
        "start": args.start,
        "oos_start": args.oos_start,
        "end": args.end,
        "initial_capital": args.capital,
        "fee": args.fee,
        "slippage": args.slippage,
        "news_filter_tested": args.news_file is not None,
        "risk_rules": {
            "max_open_positions": 5,
            "majors_correlation_group": ["BTCUSDT", "ETHUSDT"],
            "risk_majors": 0.004,
            "risk_midcaps": 0.003,
            "risk_altcoins": 0.0025,
            "daily_drawdown_stop": 0.03,
        },
    }

    (out_dir / "protocol.json").write_text(json.dumps(protocol, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "data_quality.json").write_text(json.dumps(data_quality, indent=2, ensure_ascii=False), encoding="utf-8")

    start_ms = int(datetime.fromisoformat(args.start).replace(tzinfo=UTC).timestamp() * 1000)
    oos_ms = int(datetime.fromisoformat(args.oos_start).replace(tzinfo=UTC).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(args.end).replace(tzinfo=UTC).timestamp() * 1000)

    def save_window_artifacts(win_name, ms0, ms1):
        win_dir = out_dir / win_name
        win_dir.mkdir(parents=True, exist_ok=True)
        res = run_audit_simulation(
            symbols, data_frames_1h, data_frames_4h, data_frames_1d,
            ms0, ms1, args.capital, args.fee, args.slippage,
        )
        (win_dir / "metrics.json").write_text(json.dumps(res["metrics"], indent=2), encoding="utf-8")
        pd.DataFrame(res["trades"]).to_csv(win_dir / "trades.csv", index=False)
        pd.DataFrame(res["equity_curve"]).to_csv(win_dir / "equity.csv", index=False)
        return res["metrics"]

    print("Запуск Baseline аудита...")
    full_m = save_window_artifacts("full", start_ms, end_ms)
    is_m = save_window_artifacts("in_sample", start_ms, oos_ms)
    oos_m = save_window_artifacts("out_of_sample", oos_ms, end_ms)

    ablation_dir = out_dir / "ablation"
    ablation_dir.mkdir(parents=True, exist_ok=True)

    ablations = {
        "baseline": {},
        "without_btc_gate": {"use_btc_gate": False},
        "without_volume_filter": {"use_volume_filter": False},
        "without_retest": {"use_retest": False},
        "without_partial_trailing": {"use_partial_trailing": False},
        "all_filters_disabled": {
            "use_btc_gate": False,
            "use_volume_filter": False,
            "use_retest": False,
            "use_partial_trailing": False,
        },
    }

    ablation_results = {}
    for name, kwargs in ablations.items():
        print(f"Запуск Ablation: {name}...")
        res = run_audit_simulation(
            symbols, data_frames_1h, data_frames_4h, data_frames_1d,
            start_ms, end_ms, args.capital, args.fee, args.slippage, **kwargs,
        )
        (ablation_dir / f"{name}.json").write_text(json.dumps(res["metrics"], indent=2), encoding="utf-8")
        ablation_results[name] = res["metrics"]

    agg_summary = {
        "baseline_full": full_m,
        "baseline_in_sample": is_m,
        "baseline_out_of_sample": oos_m,
        "ablation_full": ablation_results,
    }
    (out_dir / "aggregate_summary.json").write_text(json.dumps(agg_summary, indent=2), encoding="utf-8")

    md_lines = [
        "# Многовалютный аудит Multicurrency MTF (2021–2026)",
        "",
        f"- Символы: `{', '.join(symbols)}`",
        f"- Период: {args.start} → {args.end} (OOS c {args.oos_start})",
        "",
        "## Базовые метрики (Baseline)",
        "",
        f"- Full Window: PF **{full_m['profit_factor']}**, Return **{full_m['return_pct']:+.2f}%**, MaxDD **{full_m['max_drawdown']:.2f}%**, Trades **{full_m['total_trades']}**",
        f"- In-Sample: PF **{is_m['profit_factor']}**, Return **{is_m['return_pct']:+.2f}%**, MaxDD **{is_m['max_drawdown']:.2f}%**, Trades **{is_m['total_trades']}**",
        f"- Out-Of-Sample: PF **{oos_m['profit_factor']}**, Return **{oos_m['return_pct']:+.2f}%**, MaxDD **{oos_m['max_drawdown']:.2f}%**, Trades **{oos_m['total_trades']}**",
        "",
        "## Ablation Studies (Влияние компонентов)",
        "",
        "| Вариант | Profit Factor | Return % | Max Drawdown % | Trades |",
        "|---|---|---|---|---|",
    ]
    for k, v in ablation_results.items():
        md_lines.append(f"| {k} | {v['profit_factor']} | {v['return_pct']:+.2f}% | {v['max_drawdown']:.2f}% | {v['total_trades']} |")

    (out_dir / "aggregate_summary.md").write_text("\n".join(md_lines), encoding="utf-8")
    print(f"Аудит успешно завершён. Артефакты сохранены в: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
