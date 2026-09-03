#!/usr/bin/env python3
"""Исследование бесплатных обучающих стратегий на истории BTC/USDT.

Берёт правила из бесплатных учебных материалов (Babypips School of
Pipsology, Investopedia, ChartSchool, Zerodha Varsity, оригинальные
правила Turtle Trading, Connors RSI-2, материалы John Bollinger и
J. Welles Wilder, академический time-series momentum) и честно гоняет их
на реальных свечах Binance (data/BTCUSDT_{1h,4h}.csv) за последние 2 года.

Честность исполнения:
- сигнал считается по закрытию бара i, вход — по открытию бара i+1;
- стоп/тейк проверяются внутри бара и исполняются по цене уровня;
- комиссия 0.1% и проскальзывание 0.05% на каждую сторону;
- одна позиция одновременно; ноутбук не заглядывает в будущее.

Устойчивость: окно делится пополам, правило должно быть не-убыточным
(или почти) в обеих половинах, а не только «в среднем».

Итог: reports/free_strategies/summary.{md,json} + покильная статистика.
Бумажная проверка, без реальных денег.

Пример:
    python scripts/research_free_strategies.py --years 2 --capital 10000
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Издержки: Binance taker 0.1% + проскальзывание 0.05% на каждую сторону.
FEE = 0.001
SLIPPAGE = 0.0005

# Доля капитала на сделку (для сопоставимости правил между собой).
POSITION_FRACTION = 0.10


# ---------------------------------------------------------------------
# Индикаторы (каузальные: не заглядывают в будущее)
# ---------------------------------------------------------------------
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def rsi(close: pd.Series, n: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def macd_lines(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    line = ema(close, 12) - ema(close, 26)
    signal = line.ewm(span=9, adjust=False).mean()
    return line, signal


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    m = sma(close, n)
    sd = close.rolling(n).std(ddof=0)
    return m + k * sd, m, m - k * sd


def keltner(df: pd.DataFrame, n: int = 20, k: float = 2.0):
    m = ema(df["close"], n)
    a = atr(df, 10)
    return m + k * a, m, m - k * a


def _wilder(values: np.ndarray, n: int) -> np.ndarray:
    """Сглаживание Уайлдера: s = s − s/n + v/n, посев средним первых n."""
    out = np.empty_like(values, dtype=float)
    if len(values) >= n:
        seed = float(np.mean(values[:n]))
    elif len(values):
        seed = float(np.mean(values))
    else:
        seed = 0.0
    s = seed
    for i, v in enumerate(values):
        s = s - s / n + v / n
        out[i] = s
    return out


def adx_di(df: pd.DataFrame, n: int = 14):
    h, l = df["high"], df["low"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = atr(df, n).replace(0, np.nan)
    plus_di = 100 * _wilder(plus_dm.values, n) / a.values
    minus_di = 100 * _wilder(minus_dm.values, n) / a.values
    denom = plus_di + minus_di
    dx = np.where(denom > 0, 100 * np.abs(plus_di - minus_di) / np.where(denom > 0, denom, 1), 0.0)
    adx = _wilder(dx, n)
    return (
        pd.Series(adx, index=df.index).fillna(0),
        pd.Series(plus_di, index=df.index).fillna(0),
        pd.Series(minus_di, index=df.index).fillna(0),
    )


def donchian(df: pd.DataFrame, n: int) -> tuple[pd.Series, pd.Series]:
    """Канал Дончиана за ПРОШЛЫЕ n баров (без текущего)."""
    return df["high"].rolling(n).max().shift(1), df["low"].rolling(n).min().shift(1)


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> pd.Series:
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    a = atr(df, n).values
    basic = (h + l) / 2.0
    upper = basic + mult * a
    lower = basic - mult * a
    trend = np.zeros(len(df))
    for i in range(1, len(df)):
        if basic[i] + mult * a[i] < upper[i - 1] or c[i - 1] > upper[i - 1]:
            upper[i] = basic[i] + mult * a[i]
        else:
            upper[i] = upper[i - 1]
        if basic[i] - mult * a[i] > lower[i - 1] or c[i - 1] < lower[i - 1]:
            lower[i] = basic[i] - mult * a[i]
        else:
            lower[i] = lower[i - 1]
        if trend[i - 1] <= 0 and c[i] > upper[i - 1]:
            trend[i] = 1
        elif trend[i - 1] >= 0 and c[i] < lower[i - 1]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]
    return pd.Series(trend, index=df.index)


def ichimoku(df: pd.DataFrame):
    h, l, c = df["high"], df["low"], df["close"]
    tenkan = (h.rolling(9).max() + l.rolling(9).min()) / 2
    kijun = (h.rolling(26).max() + l.rolling(26).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(26)
    senkou_b = ((h.rolling(52).max() + l.rolling(52).min()) / 2).shift(26)
    cloud_top = pd.concat([senkou_a, senkou_b], axis=1).max(axis=1)
    cloud_bot = pd.concat([senkou_a, senkou_b], axis=1).min(axis=1)
    # Каузальная версия подтверждения chikou: цена выше, чем 26 баров назад.
    chikou_ok = c > c.shift(26)
    return tenkan, kijun, cloud_top, cloud_bot, chikou_ok


# ---------------------------------------------------------------------
# Правила (сигналы). Каждое возвращает (desired, конфиг стопов/холда).
# desired: -1/0/+1 на закрытии каждого бара.
# ---------------------------------------------------------------------
def _flip_flop(sig: pd.Series, keep: float = 0.0) -> pd.Series:
    """Конвертация -1/0/1 без шума: нули заполняются предыдущим значением."""
    out = sig.where(sig != 0)
    return out.ffill().fillna(0.0).astype(int)


def rule_turtle(df: pd.DataFrame):
    up20, dn20 = donchian(df, 20)
    up55, dn55 = donchian(df, 55)
    dn10_exit = df["low"].rolling(10).min().shift(1)
    up10_exit = df["high"].rolling(10).max().shift(1)
    c = df["close"]
    long_sig = (c > up20) | (c > up55)
    short_sig = (c < dn20) | (c < dn55)
    long_exit = c < dn10_exit
    short_exit = c > up10_exit
    # приоритет: сначала срабатывает выход из противоположной стороны
    long = long_sig.where(~long_exit, 0)
    short = short_sig.where(~short_exit, 0)
    sig = pd.Series(np.where(long, 1, np.where(short, -1, np.nan)), index=df.index)
    return _flip_flop(sig), dict(stop_mult=2.0, take_mult=3.5, max_hold=300)


def rule_rsi2(df: pd.DataFrame, entry_long: float, entry_short: float):
    c = df["close"]
    r = rsi(c, 2)
    mid = sma(c, 5)
    long = r < entry_long
    short = r > entry_short
    exit_long = (r > 55) | (c > mid)
    exit_short = (r < 45) | (c < mid)
    long = long.where(~exit_long, 0)
    short = short.where(~exit_short, 0)
    sig = pd.Series(np.where(long, 1, np.where(short, -1, np.nan)), index=df.index)
    return _flip_flop(sig), dict(stop_mult=3.0, take_mult=None, max_hold=120)


def rule_bb_fade(df: pd.DataFrame):
    upper, mid, lower = bollinger(df["close"], 20, 2.0)
    c = df["close"]
    long = c < lower
    short = c > upper
    exit_long = c > mid
    exit_short = c < mid
    long = long.where(~exit_long, 0)
    short = short.where(~exit_short, 0)
    sig = pd.Series(np.where(long, 1, np.where(short, -1, np.nan)), index=df.index)
    return _flip_flop(sig), dict(stop_mult=3.0, take_mult=None, max_hold=200)


def rule_bb_squeeze(df: pd.DataFrame):
    upper, mid, lower = bollinger(df["close"], 20, 2.0)
    width = (upper - lower) / mid
    squeeze = width < width.rolling(125).quantile(0.2)
    c = df["close"]
    long = squeeze & (c > upper)
    short = squeeze & (c < lower)
    exit_long = c < mid
    exit_short = c > mid
    long = long.where(~exit_long, 0)
    short = short.where(~exit_short, 0)
    sig = pd.Series(np.where(long, 1, np.where(short, -1, np.nan)), index=df.index)
    return _flip_flop(sig), dict(stop_mult=2.5, take_mult=4.0, max_hold=200)


def rule_macd(df: pd.DataFrame):
    line, signal = macd_lines(df["close"])
    sig = pd.Series(np.where(line > signal, 1, -1), index=df.index)
    return sig, dict(stop_mult=None, take_mult=None, max_hold=500)


def rule_golden_cross(df: pd.DataFrame):
    c = df["close"]
    sig = pd.Series(np.where(sma(c, 50) > sma(c, 200), 1, 0), index=df.index)
    return sig, dict(stop_mult=None, take_mult=None, max_hold=500)


def rule_engulfing(df: pd.DataFrame):
    c, o = df["close"], df["open"]
    trend = ema(c, 50)
    bull = (o.shift(1) > c.shift(1)) & (c > o) & (c >= o.shift(1)) & (o <= c.shift(1))
    bear = (c.shift(1) > o.shift(1)) & (o > c) & (o >= c.shift(1)) & (c <= o.shift(1))
    long = bull & (c > trend)
    short = bear & (c < trend)
    exit_long = bear | (c < trend)
    exit_short = bull | (c > trend)
    long = long.where(~exit_long, 0)
    short = short.where(~exit_short, 0)
    sig = pd.Series(np.where(long, 1, np.where(short, -1, np.nan)), index=df.index)
    return _flip_flop(sig), dict(stop_mult=2.0, take_mult=3.0, max_hold=150)


def rule_pinbar(df: pd.DataFrame):
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body = (c - o).abs()
    lower_wick = pd.concat([o, c], axis=1).min(axis=1) - l
    upper_wick = h - pd.concat([o, c], axis=1).max(axis=1)
    hammer = (lower_wick >= 2 * body) & (body > 0) & (upper_wick <= 0.5 * body)
    inv_hammer = (upper_wick >= 2 * body) & (body > 0) & (lower_wick <= 0.5 * body)
    at_extreme_low = l == l.rolling(20).min()
    at_extreme_high = h == h.rolling(20).max()
    # Подтверждение: закрытие следующего бара выше/ниже экстремума пин-бара.
    long = hammer.shift(1) & at_extreme_low.shift(1) & (c > h.shift(1))
    short = inv_hammer.shift(1) & at_extreme_high.shift(1) & (c < l.shift(1))
    # Выход: противоположный пин-бар или удержание до стопа/тейка.
    exit_long = short
    exit_short = long
    long = long.where(~exit_long, 0)
    short = short.where(~exit_short, 0)
    sig = pd.Series(np.where(long, 1, np.where(short, -1, np.nan)), index=df.index)
    return _flip_flop(sig), dict(stop_mult=2.0, take_mult=3.0, max_hold=120)


def rule_three_soldiers(df: pd.DataFrame):
    o, c = df["open"], df["close"]
    bull3 = (
        (c > o) & (c.shift(1) > o.shift(1)) & (c.shift(2) > o.shift(2))
        & (c > c.shift(1)) & (c.shift(1) > c.shift(2))
        & (o > o.shift(1)) & (o.shift(1) > o.shift(2))
    )
    bear3 = (
        (o > c) & (o.shift(1) > c.shift(1)) & (o.shift(2) > c.shift(2))
        & (c < c.shift(1)) & (c.shift(1) < c.shift(2))
        & (o < o.shift(1)) & (o.shift(1) < o.shift(2))
    )
    long = bull3
    short = bear3
    sig = pd.Series(np.where(long, 1, np.where(short, -1, np.nan)), index=df.index)
    return _flip_flop(sig), dict(stop_mult=1.5, take_mult=2.5, max_hold=60)


def rule_ichimoku(df: pd.DataFrame):
    tenkan, kijun, cloud_top, cloud_bot, chikou_ok = ichimoku(df)
    c = df["close"]
    long = (c > cloud_top) & (tenkan > kijun) & chikou_ok
    short = (c < cloud_bot) & (tenkan < kijun) & (~chikou_ok)
    sig = pd.Series(np.where(long, 1, np.where(short, -1, np.nan)), index=df.index)
    return _flip_flop(sig), dict(stop_mult=None, take_mult=None, max_hold=500)


def rule_adx(df: pd.DataFrame):
    adx, plus_di, minus_di = adx_di(df, 14)
    long = (adx > 25) & (plus_di > minus_di)
    short = (adx > 25) & (minus_di > plus_di)
    sig = pd.Series(np.where(long, 1, np.where(short, -1, np.nan)), index=df.index)
    return _flip_flop(sig), dict(stop_mult=None, take_mult=None, max_hold=500)


def rule_supertrend(df: pd.DataFrame):
    sig = supertrend(df, 10, 3.0)
    return sig, dict(stop_mult=None, take_mult=None, max_hold=500)


def rule_keltner_fade(df: pd.DataFrame):
    upper, mid, lower = keltner(df, 20, 2.0)
    c = df["close"]
    long = c < lower
    short = c > upper
    exit_long = c > mid
    exit_short = c < mid
    long = long.where(~exit_long, 0)
    short = short.where(~exit_short, 0)
    sig = pd.Series(np.where(long, 1, np.where(short, -1, np.nan)), index=df.index)
    return _flip_flop(sig), dict(stop_mult=2.5, take_mult=None, max_hold=200)


def rule_ema_pullback(df: pd.DataFrame):
    c = df["close"]
    e200 = ema(c, 200)
    e20 = ema(c, 20)
    uptrend = c > e200
    downtrend = c < e200
    touch20 = (df["low"] <= e20) & (c > e20)
    touch20_short = (df["high"] >= e20) & (c < e20)
    long = uptrend & touch20 & (c > o_prev(df))
    short = downtrend & touch20_short & (c < o_prev(df))
    exit_long = c < e20
    exit_short = c > e20
    long = long.where(~exit_long, 0)
    short = short.where(~exit_short, 0)
    sig = pd.Series(np.where(long, 1, np.where(short, -1, np.nan)), index=df.index)
    return _flip_flop(sig), dict(stop_mult=2.0, take_mult=None, max_hold=150)


def o_prev(df: pd.DataFrame) -> pd.Series:
    return df["open"].shift(1)


def rule_ts_momentum(df: pd.DataFrame, lookback_bars: int, band: float = 0.02):
    c = df["close"]
    ret = c / c.shift(lookback_bars) - 1
    sig = pd.Series(np.where(ret > band, 1, np.where(ret < -band, -1, np.nan)), index=df.index)
    return _flip_flop(sig), dict(stop_mult=None, take_mult=None, max_hold=500)


# ---------------------------------------------------------------------
# Раннер
# ---------------------------------------------------------------------
@dataclass
class RunOutcome:
    trades: int
    wins: int
    win_rate: float
    profit_factor: float
    net_usdt: float
    ret_pct: float
    max_dd_pct: float
    avg_trade_usdt: float
    by_reason: dict


def run_rule(
    df: pd.DataFrame,
    desired: pd.Series,
    stop_mult: float | None,
    take_mult: float | None,
    max_hold: int,
    capital: float = 10000.0,
    atr_values: pd.Series | None = None,
) -> RunOutcome:
    """Event-цикл: сигнал закрытия бара i исполняется открытием бара i+1."""
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    if atr_values is not None:
        a = atr_values.values
    else:
        a = atr(df, 14).values
    d = desired.values.astype(int)

    pos = 0
    entry_px = 0.0
    notional = 0.0
    sl = tp = 0.0
    entry_i = -1
    realized = capital
    equity = capital
    peak = capital
    max_dd = 0.0
    n_trades = 0
    wins = 0
    gross_win = 0.0
    gross_loss = 0.0
    by_reason: dict[str, int] = {}

    def close_pos(px: float, reason: str):
        nonlocal realized, n_trades, wins, gross_win, gross_loss
        if pos == 0:
            return
        r = (px / entry_px - 1.0) if pos == 1 else (1.0 - px / entry_px)
        pnl = (r - 2 * FEE - 2 * SLIPPAGE) * notional
        realized += pnl
        n_trades += 1
        if pnl > 0:
            wins += 1
            gross_win += pnl
        else:
            gross_loss += -pnl
        by_reason[reason] = by_reason.get(reason, 0) + 1

    for i in range(1, len(df)):
        target = d[i - 1]
        if target != pos:
            px = o[i]
            close_pos(px, "signal")
            pos = target
            entry_px = px
            entry_i = i
            # Номинал — доля текущего капитала (реализованного).
            notional = POSITION_FRACTION * max(realized, 1.0)
            if pos != 0 and stop_mult is not None:
                sl = px - stop_mult * a[i - 1] if pos == 1 else px + stop_mult * a[i - 1]
            else:
                sl = 0.0
            if pos != 0 and take_mult is not None:
                tp = px + take_mult * a[i - 1] if pos == 1 else px - take_mult * a[i - 1]
            else:
                tp = 0.0
        elif pos != 0:
            # внутрибарные стоп/тейк (стоп приоритетнее при гэпе через оба)
            if (sl > 0 and pos == 1 and l[i] <= sl) or (sl > 0 and pos == -1 and h[i] >= sl):
                close_pos(sl, "stop")
                pos = 0
            elif (tp > 0 and pos == 1 and h[i] >= tp) or (tp > 0 and pos == -1 and l[i] <= tp):
                close_pos(tp, "take")
                pos = 0
            elif max_hold and i - entry_i >= max_hold:
                close_pos(c[i], "timeout")
                pos = 0

        if pos != 0:
            equity = realized + (c[i] / entry_px - 1.0) * notional if pos == 1 else realized + (1.0 - c[i] / entry_px) * notional
        else:
            equity = realized
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    close_pos(c[-1], "end")
    equity = realized

    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    return RunOutcome(
        trades=n_trades,
        wins=wins,
        win_rate=wins / n_trades * 100 if n_trades else 0.0,
        profit_factor=pf,
        net_usdt=equity - capital,
        ret_pct=(equity / capital - 1) * 100,
        max_dd_pct=max_dd,
        avg_trade_usdt=(equity - capital) / n_trades if n_trades else 0.0,
        by_reason=by_reason,
    )


# ---------------------------------------------------------------------
# Реестр правил
# ---------------------------------------------------------------------
RULES: list[dict] = [
    dict(key="turtle_20_55", name="Turtle breakout (Donchian 20/55)", source="Оригинальные правила Turtle Trading (бесплатный мануал Curtis Faith)",
         fn=lambda df: rule_turtle(df)),
    dict(key="rsi2_10", name="RSI(2) mean reversion 10/90", source="Connors RSI-2 (бесплатные материалы Larry Connors)",
         fn=lambda df: rule_rsi2(df, 10, 90)),
    dict(key="rsi2_5", name="RSI(2) mean reversion 5/95", source="Connors RSI-2, строгий вариант (бесплатные материалы)",
         fn=lambda df: rule_rsi2(df, 5, 95)),
    dict(key="bb_fade", name="Bollinger fade (20,2) к средней", source="Babypips / John Bollinger (bollingerbands.com), уроки BB",
         fn=lambda df: rule_bb_fade(df)),
    dict(key="bb_squeeze", name="Bollinger squeeze breakout", source="John Bollinger «Squeeze» (бесплатные статьи)",
         fn=lambda df: rule_bb_squeeze(df)),
    dict(key="macd_cross", name="MACD(12,26,9) пересечение", source="Babypips урок MACD / Investopedia",
         fn=lambda df: rule_macd(df)),
    dict(key="golden_cross", name="SMA 50/200 golden cross", source="Babypips урок Moving Averages / Investopedia",
         fn=lambda df: rule_golden_cross(df)),
    dict(key="engulfing_ema", name="Поглощение + фильтр EMA50", source="Babypips курс японских свечей (Dual patterns)",
         fn=lambda df: rule_engulfing(df)),
    dict(key="pinbar_extreme", name="Пин-бар на экстремуме 20 баров", source="Babypips Price Action (пин-бары)",
         fn=lambda df: rule_pinbar(df)),
    dict(key="three_soldiers", name="Три белых солдата / три чёрные вороны", source="Babypips курс свечей (Triple patterns) / Investopedia",
         fn=lambda df: rule_three_soldiers(df)),
    dict(key="ichimoku", name="Ichimoku Kinko Hyo (облако)", source="Babypips полный бесплатный курс Ichimoku",
         fn=lambda df: rule_ichimoku(df)),
    dict(key="adx_trend", name="ADX(14)>25 + DI-кросс", source="J. Welles Wilder (Babypips урок ADX)",
         fn=lambda df: rule_adx(df)),
    dict(key="supertrend", name="Supertrend (10,3)", source="TradingView Education / бесплатные уроки ATR-трейлинга",
         fn=lambda df: rule_supertrend(df)),
    dict(key="keltner_fade", name="Keltner fade (EMA20 ± 2·ATR10)", source="Babypips урок Keltner Channels",
         fn=lambda df: rule_keltner_fade(df)),
    dict(key="ema_pullback", name="Pullback к EMA20 в тренде EMA200", source="Babypips урок «trading pullbacks»",
         fn=lambda df: rule_ema_pullback(df)),
    dict(key="ts_momentum", name="Time-series momentum (30 дней)", source="Moskowitz, Ooi, Pedersen «Time Series Momentum» (свободный препринт)",
         fn=lambda df: rule_ts_momentum(df, 24 * 30)),
]


# ---------------------------------------------------------------------
# Оркестрация
# ---------------------------------------------------------------------
def evaluate(
    df_full: pd.DataFrame,
    start_dt: datetime,
    end_dt: datetime,
    capital: float,
    long_only: bool,
) -> dict:
    """Прогнать все правила на окне; вернуть строки отчёта и полные данные."""
    t0 = int(start_dt.timestamp() * 1000)
    t1 = int(end_dt.timestamp() * 1000)
    tmid = t0 + (t1 - t0) // 2

    rows = []
    details = {}
    atr_full = atr(df_full, 14)  # с разминкой на всей истории — для стопов
    for rule in RULES:
        desired, conf = rule["fn"](df_full)

        def run_window(
            ms0: int, ms1: int,
            desired: pd.Series = desired,
            conf: dict = conf,
        ) -> RunOutcome:
            m = (df_full["open_time"] >= ms0) & (df_full["open_time"] < ms1)
            d = desired[m]
            if long_only:
                d = d.clip(lower=0)
            return run_rule(
                df_full[m].reset_index(drop=True),
                d.reset_index(drop=True),
                conf.get("stop_mult"),
                conf.get("take_mult"),
                conf.get("max_hold", 300),
                capital=capital,
                atr_values=atr_full[m].reset_index(drop=True),
            )

        full = run_window(t0, t1)
        h1 = run_window(t0, tmid)
        h2 = run_window(tmid, t1)
        rows.append(
            {
                "key": rule["key"],
                "name": rule["name"],
                "source": rule["source"],
                "trades": full.trades,
                "win_rate": round(full.win_rate, 1),
                "pf": round(full.profit_factor, 2) if full.profit_factor != float("inf") else None,
                "ret_pct": round(full.ret_pct, 2),
                "max_dd_pct": round(full.max_dd_pct, 2),
                "h1_ret_pct": round(h1.ret_pct, 2),
                "h2_ret_pct": round(h2.ret_pct, 2),
                "h1_pf": round(h1.profit_factor, 2) if h1.profit_factor != float("inf") else None,
                "h2_pf": round(h2.profit_factor, 2) if h2.profit_factor != float("inf") else None,
                "by_reason": full.by_reason,
            }
        )
        details[rule["key"]] = {
            "full": vars(full),
            "h1": vars(h1),
            "h2": vars(h2),
        }
    return rows, details


def render_md(rows: list[dict], timeframe: str, mode: str, bh_pct: float, window: str) -> str:
    lines = [f"## {timeframe} · режим: {mode} · окно {window} (buy&hold: {bh_pct:+.2f}%)", ""]
    lines.append("| Правило | Сделок | Win% | PF | Доход% | MaxDD% | Год1% | Год2% | Год1 PF | Год2 PF |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda x: -(x["ret_pct"] or -999)):
        pf = "∞" if r["pf"] is None else f"{r['pf']:.2f}"
        h1pf = "∞" if r["h1_pf"] is None else f"{r['h1_pf']:.2f}"
        h2pf = "∞" if r["h2_pf"] is None else f"{r['h2_pf']:.2f}"
        lines.append(
            f"| {r['name']} | {r['trades']} | {r['win_rate']:.1f} | {pf} | "
            f"{r['ret_pct']:+.2f} | {r['max_dd_pct']:.1f} | {r['h1_ret_pct']:+.2f} | "
            f"{r['h2_ret_pct']:+.2f} | {h1pf} | {h2pf} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=float, default=2.0)
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--end", default="2026-08-20")
    parser.add_argument("--data-dir", default=str(PROJECT_ROOT / "data"))
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "reports" / "free_strategies"))
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    end_dt = datetime.fromisoformat(args.end).replace(tzinfo=UTC)
    start_dt = end_dt - timedelta(days=int(args.years * 365.25))

    md_parts = [
        "# Исследование бесплатных обучающих стратегий на истории BTC/USDT",
        "",
        f"Окно: {start_dt.date()} → {end_dt.date()} ({args.years} года) · капитал {args.capital:,.0f} USDT · "
        f"риск-размер: {POSITION_FRACTION:.0%} капитала на сделку · комиссия {FEE:.2%} + "
        f"проскальзывание {SLIPPAGE:.2%} на сторону · исполнение: вход по открытию бара после сигнала.",
        "",
        "## Источники правил (бесплатные курсы и материалы)",
        "",
        "- **Babypips School of Pipsology** — бесплатный структурированный курс: свечи, фигуры, индикаторы: https://www.babypips.com/learn/forex",
        "- **Investopedia** — Guide to Technical Analysis: https://www.investopedia.com/",
        "- **TradingView Education** — бесплатные уроки и вебинары: https://www.tradingview.com/education/",
        "- **StockCharts ChartSchool** — бесплатная энциклопедия индикаторов: https://chartschool.stockcharts.com/",
        "- **Zerodha Varsity** — бесплатные модули по теханализу: https://zerodha.com/varsity/",
        "- **ThePatternSite** — бесплатный каталог свечных/графических паттернов: https://thepatternsite.com/",
        "- **Оригинальные правила Turtle Trading** — свободный мануал (Curtis Faith / Richard Dennis)",
        "- **Connors Research** — бесплатные материалы по RSI-2 (Larry Connors)",
        "- **John Bollinger** — бесплатные статьи по лентам и Squeeze: https://www.bollingerbands.com/",
        "- **J. Welles Wilder** — «New Concepts in Technical Trading Systems» (ATR/RSI/ADX)",
        "- **Moskowitz, Ooi, Pedersen** — «Time Series Momentum» (свободный препринт)",
        "",
    ]

    report = {"window": f"{start_dt.date()} → {end_dt.date()}", "timeframes": {}}
    for timeframe, fname in (("1h", "BTCUSDT_1h.csv"), ("4h", "BTCUSDT_4h.csv")):
        path = data_dir / fname
        if not path.exists():
            print(f"Нет данных: {path}")
            continue
        df = pd.read_csv(path)
        df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)

        w = df[(df["open_time"] >= int(start_dt.timestamp() * 1000)) & (df["open_time"] < int(end_dt.timestamp() * 1000))]
        bh_pct = float(w["close"].iloc[-1] / w["close"].iloc[0] - 1) * 100

        tf_report = {}
        for mode, long_only in (("long+short", False), ("long-only", True)):
            rows, details = evaluate(df, start_dt, end_dt, args.capital, long_only)
            tf_report[mode] = {"rows": rows, "details": details}
            md_parts.append(render_md(rows, timeframe, mode, bh_pct, f"{start_dt.date()} → {end_dt.date()}"))
            md_parts.append("")
        report["timeframes"][timeframe] = tf_report

    # Вывод «победителей»: устойчивость в обеих половинах окна.
    winners = []
    for tf, tfrep in report["timeframes"].items():
        for mode, data in tfrep.items():
            for r in data["rows"]:
                pf_ok = r["pf"] is not None and r["pf"] >= 1.2
                halves_ok = (
                    (r["h1_pf"] is None or r["h1_pf"] >= 0.9)
                    and (r["h2_pf"] is None or r["h2_pf"] >= 0.9)
                    and r["h1_ret_pct"] >= -2.0
                    and r["h2_ret_pct"] >= -2.0
                )
                if pf_ok and halves_ok and r["trades"] >= 20 and r["max_dd_pct"] < 30:
                    winners.append((tf, mode, r))

    md_parts.append("## Кандидаты на интеграцию (устойчивые в обеих половинах окна)")
    md_parts.append("")
    if winners:
        for tf, mode, r in winners:
            md_parts.append(
                f"- **{r['name']}** · {tf} · {mode}: PF={r['pf']:.2f}, "
                f"доход {r['ret_pct']:+.2f}% (Год1 {r['h1_ret_pct']:+.2f}% / Год2 {r['h2_ret_pct']:+.2f}%), "
                f"сделок {r['trades']}, просадка {r['max_dd_pct']:.1f}% — {r['source']}"
            )
    else:
        md_parts.append("Устойчивых кандидатов на этом окне не нашлось — интеграция нецелесообразна.")
    md_parts.append("")
    md_parts.append("> Бумажная проверка на истории. Прошлые результаты не гарантируют будущую прибыль.")

    md_text = "\n".join(md_parts)
    (out_dir / "summary.md").write_text(md_text, encoding="utf-8")
    (out_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(md_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
