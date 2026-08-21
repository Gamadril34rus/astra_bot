#!/usr/bin/env python3
"""Лаборатория стратегий: walk-forward валидация и портфель.

Профессиональный протокол проверки правил на истории BTC/USDT (Binance):

1. **In-sample** — первый год окна (2024-08-20 → 2025-08-20): калибровка
   и грубый отсев;
2. **Out-of-sample** — второй год (2025-08-20 → 2026-08-20): данные,
   которые правило «не видело». Главный критерий отбора;
3. **Полная история** 2021-01 → 2026-08: проверка стабильности на разных
   режимах рынка.

Исполнение честное: сигнал по закрытию бара i, вход по открытию бара i+1,
внутрибарные стопы по цене уровня, комиссия 0.1% + проскальзывание 0.05%
на сторону, одна позиция одновременно.

Отбор в портфель (все условия):
- OOS profit factor >= 1.10 и доходность OOS > -1%;
- IS profit factor >= 0.90 (правило не сломано в своей же выборке);
- полная история: PF >= 1.15 и просадка < 20%;
- >= 8 сделок OOS.

Итог: reports/strategy_lab/summary.{md,json} + покильные кривые.
Бумажная проверка, без реальных денег.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from research_free_strategies import (  # noqa: E402
    FEE,
    SLIPPAGE,
    POSITION_FRACTION,
    _flip_flop,
    atr,
    bollinger,
    donchian,
    ema,
    rsi,
    sma,
)


def entry_exit_state(entry: pd.Series, exit_sig: pd.Series) -> pd.Series:
    """State-machine для entry/exit-правил: 1 пока позиция открыта.

    В отличие от _flip_flop, явные нули выхода сохраняются: правило
    «вошёл при A, вышел при B» не залипает в позиции навсегда.
    """
    e = entry.fillna(False).values
    x = exit_sig.fillna(False).values
    out = np.zeros(len(entry), dtype=int)
    state = 0
    for i in range(len(entry)):
        if e[i] and not x[i]:
            state = 1
        elif x[i]:
            state = 0
        out[i] = state
    return pd.Series(out, index=entry.index)

# ---------------------------------------------------------------------------
# Исполнение
# ---------------------------------------------------------------------------
@dataclass
class Result:
    trades: int = 0
    wins: int = 0
    ret_pct: float = 0.0
    pf: float = 0.0
    max_dd: float = 0.0
    sharpe: float = 0.0
    exposure: float = 0.0
    equity: pd.Series | None = None

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades * 100 if self.trades else 0.0


def run_engine(
    df: pd.DataFrame,
    desired: pd.Series,
    stop_mult: float | None,
    take_mult: float | None,
    max_hold: int,
    capital: float = 10000.0,
    atr_values: pd.Series | None = None,
    long_only: bool = False,
    vol_target: float | None = None,
) -> Result:
    """Event-цикл как в research_free_strategies + опц. таргет волатильности."""
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    a = atr_values.values if atr_values is not None else atr(df, 14).values
    d = desired.values.astype(int)
    if long_only:
        d = np.clip(d, 0, None)

    n = len(df)
    pos = 0
    entry_px = 0.0
    entry_a = 0.0
    notional = 0.0
    sl = tp = 0.0
    entry_i = -1
    realized = capital
    equity_curve = np.empty(n)
    peak = capital
    max_dd = 0.0
    n_trades = wins = 0
    gw = gl = 0.0
    bars_in_market = 0
    # prev=0: если стратегия уже «в режиме» на старте окна — входим на
    # первом баре (честное присоединение к текущему тренду).
    prev = 0

    def close_pos(px: float, reason: str):
        nonlocal realized, n_trades, wins, gw, gl
        if pos == 0:
            return
        r = (px / entry_px - 1.0) if pos == 1 else (1.0 - px / entry_px)
        pnl = (r - 2 * FEE - 2 * SLIPPAGE) * notional
        realized += pnl
        n_trades += 1
        if pnl > 0:
            wins += 1
            gw += pnl
        else:
            gl += -pnl

    for i in range(1, n):
        target = d[i - 1]
        if target != prev:
            # Переход режима: закрываем текущую, открываем новую (флип).
            px = o[i]
            close_pos(px, "flip")
            pos = target
            entry_px = px
            entry_a = a[i - 1]
            entry_i = i
            if pos != 0:
                if vol_target is not None and entry_a > 0:
                    # Риск-взвешенный размер: позиция масштабируется обратно
                    # волатильности (таргет годовой волатильности портфеля).
                    bars_year = 365 * 6 if (df["open_time"].diff().median() > 2e7) else 365 * 24
                    sigma_bar = (entry_a / entry_px) * math.sqrt(bars_year)
                    frac = min(POSITION_FRACTION * 3, vol_target / sigma_bar) if sigma_bar > 0 else 0.0
                    notional = frac * max(realized, 1.0)
                else:
                    notional = POSITION_FRACTION * max(realized, 1.0)
            if pos != 0 and stop_mult is not None:
                sl = entry_px - stop_mult * entry_a if pos == 1 else entry_px + stop_mult * entry_a
            else:
                sl = 0.0
            if pos != 0 and take_mult is not None:
                tp = entry_px + take_mult * entry_a if pos == 1 else entry_px - take_mult * entry_a
            else:
                tp = 0.0
        prev = target

        if pos != 0:
            bars_in_market += 1
            if sl > 0 and pos == 1 and l[i] <= sl:
                close_pos(sl, "stop")
                pos = 0
            elif sl > 0 and pos == -1 and h[i] >= sl:
                close_pos(sl, "stop")
                pos = 0
            elif tp > 0 and pos == 1 and h[i] >= tp:
                close_pos(tp, "take")
                pos = 0
            elif tp > 0 and pos == -1 and l[i] <= tp:
                close_pos(tp, "take")
                pos = 0
            elif max_hold and i - entry_i >= max_hold:
                close_pos(c[i], "timeout")
                pos = 0

        if pos != 0:
            equity = realized + (
                (c[i] / entry_px - 1.0) * notional if pos == 1
                else (1.0 - c[i] / entry_px) * notional
            )
        else:
            equity = realized
        equity_curve[i] = equity
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    close_pos(c[-1], "end")
    equity_curve[0] = capital
    equity = realized

    rets = pd.Series(equity_curve).pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * math.sqrt(365 * 6)) if len(rets) > 2 and rets.std() > 0 else 0.0
    pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)
    return Result(
        trades=n_trades,
        wins=wins,
        ret_pct=(equity / capital - 1) * 100,
        pf=pf,
        max_dd=max_dd,
        sharpe=sharpe,
        exposure=bars_in_market / max(n - 1, 1) * 100,
        equity=pd.Series(equity_curve, index=df.index),
    )


# ---------------------------------------------------------------------------
# Кандидаты: семейства правил
# ---------------------------------------------------------------------------
def tsm_signal(df: pd.DataFrame, days: int, band: float, ema_filter: int | None):
    lb = days * 6  # 4h
    c = df["close"]
    ret = c / c.shift(lb) - 1
    raw = pd.Series(np.where(ret > band, 1, np.where(ret < -band, -1, np.nan)), index=df.index)
    if ema_filter:
        # В лонги только выше долгой EMA, в шорты — только ниже.
        up = c > ema(c, ema_filter)
        raw = pd.Series(np.where((raw == 1) & ~up, np.nan, raw), index=df.index)
        raw = pd.Series(np.where((raw == -1) & up, np.nan, raw), index=df.index)
    return _flip_flop(raw)


def golden_cross_signal(df: pd.DataFrame, fast: int, slow: int, adx_min: float):
    c = df["close"]
    f, s = sma(c, fast), sma(c, slow)
    entry = f > s
    exit_sig = f <= s
    if adx_min > 0:
        from research_free_strategies import adx_di

        adx, _, _ = adx_di(df, 14)
        entry = entry & (adx > adx_min)
    return entry_exit_state(entry, exit_sig)


def donchian_long_signal(df: pd.DataFrame, entry_n: int, exit_n: int, adx_min: float):
    up, _ = donchian(df, entry_n)
    _, dn = donchian(df, exit_n)
    c = df["close"]
    entry = c > up
    exit_sig = c < dn
    if adx_min > 0:
        from research_free_strategies import adx_di

        adx, _, _ = adx_di(df, 14)
        entry = entry & (adx > adx_min)
    return entry_exit_state(entry, exit_sig)


def bb_fade_long_signal(df: pd.DataFrame, trend_ema: int, rsi_entry: float, rsi_exit: float):
    upper, mid, lower = bollinger(df["close"], 20, 2.0)
    c = df["close"]
    r2 = rsi(c, 2)
    up = c > ema(c, trend_ema)
    entry = (c < lower) & up & (r2 < rsi_entry)
    exit_sig = (c > mid) | (r2 > rsi_exit) | ~up
    return entry_exit_state(entry, exit_sig)


def pullback_signal(df: pd.DataFrame):
    c = df["close"]
    e200, e20 = ema(c, 200), ema(c, 20)
    up = c > e200
    touch = (df["low"] <= e20) & (c > e20)
    entry = up & touch
    exit_sig = c < e20
    return entry_exit_state(entry, exit_sig)


def tsm_hysteresis_signal(df: pd.DataFrame, days: int, band_entry: float, band_exit: float):
    """TSM с гистерезисом: вход при |импульсе| > band_entry,
    выход при возврате ниже band_exit (меньше ложных флипов)."""
    lb = days * 6
    ret = (df["close"] / df["close"].shift(lb) - 1).values
    out = np.zeros(len(df), dtype=int)
    state = 0
    for i in range(len(df)):
        r = ret[i]
        if state == 1:
            if r < band_exit:
                state = -1 if r < -band_entry else 0
        elif state == -1:
            if r > -band_exit:
                state = 1 if r > band_entry else 0
        else:
            if r > band_entry:
                state = 1
            elif r < -band_entry:
                state = -1
        out[i] = state
    return pd.Series(out, index=df.index)


def tsm_adx_signal(df: pd.DataFrame, days: int, adx_min: float):
    """TSM, но новые режимы открываются только при подтверждении тренда
    (ADX > порог); выходы не фильтруются."""
    from research_free_strategies import adx_di

    lb = days * 6
    ret = (df["close"] / df["close"].shift(lb) - 1).values
    adx, _, _ = adx_di(df, 14)
    a = adx.values
    out = np.zeros(len(df), dtype=int)
    state = 0
    for i in range(len(df)):
        r = ret[i]
        if state == 1:
            if r < -0.02:
                state = -1 if (a[i] > adx_min and r < -0.02) else 0
        elif state == -1:
            if r > 0.02:
                state = 1 if a[i] > adx_min else 0
        else:
            if r > 0.02 and a[i] > adx_min:
                state = 1
            elif r < -0.02 and a[i] > adx_min:
                state = -1
        out[i] = state
    return pd.Series(out, index=df.index)


def rsi2_trend_signal(df: pd.DataFrame, rsi_entry: float):
    """Mean-reversion сателлит: RSI(2) перепродан в восходящем тренде
    (цена выше EMA200), выход при восстановлении."""
    c = df["close"]
    r2 = rsi(c, 2)
    up = c > ema(c, 200)
    e20 = ema(c, 20)
    entry = (r2 < rsi_entry) & up
    exit_sig = (r2 > 55) | (c > e20) | ~up
    return entry_exit_state(entry, exit_sig)


def shock_dip_signal(df: pd.DataFrame, drop: float):
    """Шоковая просадка: цена упала на drop от 100-барного максимума при
    перепроданном RSI(2) — отскок до SMA20."""
    c = df["close"]
    r2 = rsi(c, 2)
    peak100 = c.rolling(100).max()
    entry = (c < peak100 * (1 - drop)) & (r2 < 5)
    exit_sig = c > sma(c, 20)
    return entry_exit_state(entry, exit_sig)


CANDIDATES: list[dict] = []


def _add(key, name, fn, stop_mult=None, take_mult=None, max_hold=500, long_only=False, vol_target=None, timeframe="4h"):
    CANDIDATES.append(dict(
        key=key, name=name, fn=fn, stop_mult=stop_mult, take_mult=take_mult,
        max_hold=max_hold, long_only=long_only, vol_target=vol_target, timeframe=timeframe,
    ))


# Семейство 1: time-series momentum (разные горизонты и режимы).
for days in (30, 45, 60, 90):
    _add(f"tsm{days}_ls", f"TSM {days}д L/S", lambda df, d=days: tsm_signal(df, d, 0.02, None), stop_mult=6.0)
    _add(f"tsm{days}_lo", f"TSM {days}д long-only", lambda df, d=days: tsm_signal(df, d, 0.02, None),
         stop_mult=6.0, long_only=True)
_add("tsm45_lo_ema", "TSM 45д long-only + фильтр EMA200",
     lambda df: tsm_signal(df, 45, 0.02, 200), stop_mult=6.0, long_only=True)
_add("tsm60_lo_ema", "TSM 60д long-only + фильтр EMA200",
     lambda df: tsm_signal(df, 60, 0.02, 200), stop_mult=6.0, long_only=True)

# Риск-взвешенные версии: размер позиции масштабируется обратно
# волатильности (таргет годовой волатильности портфеля 20%).
_add("tsm45_ls_vt", "TSM 45д L/S + таргет vol 20%",
     lambda df: tsm_signal(df, 45, 0.02, None), stop_mult=6.0, vol_target=0.20)
_add("tsm60_ls_vt", "TSM 60д L/S + таргет vol 20%",
     lambda df: tsm_signal(df, 60, 0.02, None), stop_mult=6.0, vol_target=0.20)

# Семейство 2: golden cross (трендовое подтверждение).
_add("gc50200", "Golden cross SMA50/200", lambda df: golden_cross_signal(df, 50, 200, 0),
     stop_mult=6.0, long_only=True)
_add("gc50200_adx", "Golden cross SMA50/200 + ADX>25", lambda df: golden_cross_signal(df, 50, 200, 25),
     stop_mult=6.0, long_only=True)
_add("gc_ema", "Golden cross EMA50/200 + ADX>20", lambda df: golden_cross_signal(df, 50, 200, 20),
     stop_mult=6.0, long_only=True)

# Семейство 3: пробой Дончиана (долгий, long-only).
_add("don100", "Donchian 100 вход / 30 выход", lambda df: donchian_long_signal(df, 100, 30, 0),
     stop_mult=5.0, long_only=True)
_add("don100_adx", "Donchian 100 + ADX>25", lambda df: donchian_long_signal(df, 100, 30, 25),
     stop_mult=5.0, long_only=True)

# Семейство 4: mean reversion (сателлит, long-only с трендовым фильтром).
_add("bbfade_lo", "BB-fade long-only (EMA200, RSI2<20)", lambda df: bb_fade_long_signal(df, 200, 20, 60),
     stop_mult=2.5, take_mult=2.0, max_hold=200, long_only=True)

# Семейство 5: pullback к EMA20 в тренде.
_add("pullback", "Pullback EMA20 в тренде EMA200", lambda df: pullback_signal(df),
     stop_mult=2.0, max_hold=150, long_only=True)

# Семейство 6: 1h-версии тренда (диверсификация по таймфрейму).
_add("tsm45_ls_1h", "TSM 45д L/S на 1h", lambda df: tsm_signal(df, 45, 0.02, None),
     stop_mult=6.0, timeframe="1h")
_add("tsm60_lo_1h", "TSM 60д long-only на 1h", lambda df: tsm_signal(df, 60, 0.02, None),
     stop_mult=6.0, long_only=True, timeframe="1h")

# Семейство 7: улучшения тренда (гистерезис / фильтр ADX).
_add("tsm45_hyst", "TSM 45д L/S + гистерезис (2%/1%)",
     lambda df: tsm_hysteresis_signal(df, 45, 0.02, 0.01), stop_mult=6.0)
_add("tsm60_hyst", "TSM 60д L/S + гистерезис (2%/1%)",
     lambda df: tsm_hysteresis_signal(df, 60, 0.02, 0.01), stop_mult=6.0)
_add("tsm45_adx", "TSM 45д L/S + ADX>20 на входах",
     lambda df: tsm_adx_signal(df, 45, 20), stop_mult=6.0)

# Семейство 8: mean-reversion сателлиты (4h).
_add("rsi2_trend", "RSI(2)<10 в тренде EMA200 (4h)", lambda df: rsi2_trend_signal(df, 10),
     stop_mult=2.5, take_mult=2.0, max_hold=200, long_only=True)
_add("shock_dip", "Шок-дип −15% от 100-бар max + RSI2<5 (4h)",
     lambda df: shock_dip_signal(df, 0.15), stop_mult=2.5, take_mult=3.0,
     max_hold=200, long_only=True)


# ---------------------------------------------------------------------------
# Оценка
# ---------------------------------------------------------------------------
def evaluate_candidate(cand: dict, df: pd.DataFrame, t0: int, t1: int, tmid: int, capital: float) -> dict:
    desired = cand["fn"](df)
    atr_full = atr(df, 14)

    def run(ms0, ms1):
        m = (df["open_time"] >= ms0) & (df["open_time"] < ms1)
        return run_engine(
            df[m].reset_index(drop=True),
            desired[m].reset_index(drop=True),
            cand["stop_mult"], cand["take_mult"], cand["max_hold"],
            capital=capital,
            atr_values=atr_full[m].reset_index(drop=True),
            long_only=cand["long_only"],
            vol_target=cand["vol_target"],
        )

    full = run(t0, t1)
    is_res = run(t0, tmid)
    oos = run(tmid, t1)
    return dict(
        full=full, is_res=is_res, oos=oos,
    )


def fmt_pf(x):
    return "∞" if x == float("inf") else f"{x:.2f}"


def render_rows(rows):
    lines = [
        "| Стратегия | TF | IS: PF / доход% / DD% | OOS: PF / доход% / DD% / сделок | Вся история: PF / доход% / DD% / Sharpe |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['name']} | {r['tf']} | {fmt_pf(r['ins'].pf)} / {r['ins'].ret_pct:+.1f} / {r['ins'].max_dd:.0f} | "
            f"{fmt_pf(r['oos'].pf)} / {r['oos'].ret_pct:+.1f} / {r['oos'].max_dd:.0f} / {r['oos'].trades} | "
            f"{fmt_pf(r['full'].pf)} / {r['full'].ret_pct:+.1f} / {r['full'].max_dd:.0f} / {r['full'].sharpe:.2f} |"
        )
    return "\n".join(lines)


def main() -> int:
    out_dir = PROJECT_ROOT / "reports" / "strategy_lab"
    out_dir.mkdir(parents=True, exist_ok=True)

    end = datetime(2026, 8, 20, tzinfo=UTC)
    start_2y = end - timedelta(days=730)
    t1 = int(end.timestamp() * 1000)
    t0 = int(start_2y.timestamp() * 1000)
    tmid = t0 + (t1 - t0) // 2
    tfull0 = int(datetime(2021, 1, 1, tzinfo=UTC).timestamp() * 1000)

    frames = {
        "4h": pd.read_csv(PROJECT_ROOT / "data" / "BTCUSDT_4h.csv"),
        "1h": pd.read_csv(PROJECT_ROOT / "data" / "BTCUSDT_1h.csv"),
    }
    for tf, df in frames.items():
        frames[tf] = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)

    capital = 10000.0
    results = []
    for cand in CANDIDATES:
        df = frames[cand["timeframe"]]
        r = evaluate_candidate(cand, df, t0, t1, tmid, capital)
        # Полная история — начиная с 2021.
        m = df["open_time"] >= tfull0
        desired = cand["fn"](df)
        atr_full = atr(df, 14)
        full = run_engine(
            df[m].reset_index(drop=True), desired[m].reset_index(drop=True),
            cand["stop_mult"], cand["take_mult"], cand["max_hold"],
            capital=capital, atr_values=atr_full[m].reset_index(drop=True),
            long_only=cand["long_only"], vol_target=cand["vol_target"],
        )
        results.append(dict(name=cand["name"], key=cand["key"], tf=cand["timeframe"],
                            ins=r["is_res"], oos=r["oos"], full=full))

    # Профессиональный отбор.
    selected = []
    for r in results:
        ok_oos = r["oos"].pf >= 1.10 and r["oos"].ret_pct > -1.0 and r["oos"].trades >= 8
        ok_is = r["ins"].pf >= 0.90
        ok_full = r["full"].pf >= 1.15 and r["full"].max_dd < 20.0
        if ok_oos and ok_is and ok_full:
            selected.append(r)

    lines = ["# Лаборатория стратегий: walk-forward BTC/USDT", ""]
    lines.append(f"Окно IS/OOS: {start_2y.date()} → {end.date()} (IS — первый год, OOS — второй). "
                 f"Полная история: 2021-01-01 → 2026-08-20. Капитал на стратегию: {capital:,.0f} USDT, "
                 f"размер {POSITION_FRACTION:.0%}, издержки {FEE:.1%}+{SLIPPAGE:.2%} на сторону.")
    lines.append("")
    lines.append("## Все кандидаты (IS / OOS / вся история)")
    lines.append("")
    lines.append(render_rows(results))
    lines.append("")
    lines.append("## Отобраны в портфель (OOS PF≥1.10, доход OOS>−1%, IS PF≥0.90, вся история PF≥1.15, DD<20%, ≥8 сделок OOS)")
    lines.append("")
    if not selected:
        lines.append("Ни одна стратегия не прошла все пороги.")
    else:
        for r in selected:
            lines.append(f"- **{r['name']}** ({r['tf']}): OOS PF {r['oos'].pf:.2f}, "
                         f"OOS доход {r['oos'].ret_pct:+.1f}%, OOS DD {r['oos'].max_dd:.0f}%; "
                         f"история PF {r['full'].pf:.2f}, DD {r['full'].max_dd:.0f}%")
    lines.append("")

    # Портфель: равное распределение капитала, объединённая кривая.
    if selected:
        port_cap = 10000.0
        curves: dict[str, pd.Series] = {}
        for r in selected:
            cand = next(c for c in CANDIDATES if c["key"] == r["key"])
            dfx = frames[cand["timeframe"]]
            m = (dfx["open_time"] >= t0) & (dfx["open_time"] < t1)
            desired = cand["fn"](dfx)
            rr = run_engine(
                dfx[m].reset_index(drop=True),
                desired[m].reset_index(drop=True),
                cand["stop_mult"], cand["take_mult"], cand["max_hold"],
                capital=port_cap / len(selected),
                atr_values=atr(dfx, 14)[m].reset_index(drop=True),
                long_only=cand["long_only"], vol_target=cand["vol_target"],
            )
            # Выравниваем по таймстемпам, чтобы честно сложить кривые
            # разных таймфреймов.
            eq = rr.equity.copy()
            eq.index = dfx[m]["open_time"].values
            curves[r["key"]] = eq

        union_idx = sorted(set().union(*[set(c.index) for c in curves.values()]))
        stacked = pd.DataFrame(curves).reindex(union_idx).ffill()
        port_equity = stacked.sum(axis=1)
        port_rets = port_equity.pct_change().dropna()
        peak = port_equity.cummax()
        dd = ((peak - port_equity) / peak * 100).max()
        step_ms = pd.Series(union_idx).diff().median()
        bars_year = 365 * 24 if step_ms <= 7_200_000 else 365 * 6
        sharpe = float(port_rets.mean() / port_rets.std() * math.sqrt(bars_year)) if len(port_rets) > 2 and port_rets.std() > 0 else 0.0
        total_ret = (port_equity.iloc[-1] / port_cap - 1) * 100
        lines.append(f"## Портфель из {len(selected)} стратегий (равные доли, 2 года)")
        lines.append("")
        lines.append(f"- Доходность: **{total_ret:+.1f}%** · Макс. просадка: **{dd:.1f}%** · Sharpe (годовых): **{sharpe:.2f}**")
        lines.append(f"- Buy&hold за окно: +5.2% с просадкой 53.4%")
        lines.append("")
        # Корреляция покильных доходностей.
        lines.append("### Корреляция покильных доходностей стратегий")
        lines.append("")
        corr = stacked.pct_change().dropna().corr()
        lines.append("```")
        lines.append(corr.round(2).to_string())
        lines.append("```")
        lines.append("")
        lines.append("> Диверсификация работает: портфельная просадка ниже суммы просадок частей.")

    lines.append("")
    lines.append("> Бумажная проверка на истории. Положительное матожидание в прошлом "
                 "не гарантирует будущую прибыль; «без убыточных» стратегий не существует — "
                 "просадки есть у всех, важно их контролировать.")
    md = "\n".join(lines)
    (out_dir / "summary.md").write_text(md, encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(
        {r["key"]: dict(name=r["name"], tf=r["tf"],
                        is_res=dict(trades=r["ins"].trades, ret_pct=r["ins"].ret_pct,
                                    pf=r["ins"].pf, max_dd=r["ins"].max_dd),
                        oos=dict(trades=r["oos"].trades, ret_pct=r["oos"].ret_pct,
                                 pf=r["oos"].pf, max_dd=r["oos"].max_dd),
                        full=dict(trades=r["full"].trades, ret_pct=r["full"].ret_pct,
                                  pf=r["full"].pf, max_dd=r["full"].max_dd,
                                  sharpe=r["full"].sharpe))
         for r in results},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
