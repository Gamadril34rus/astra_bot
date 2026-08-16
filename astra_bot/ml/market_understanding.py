"""ASTRA BOT — Market Understanding feature engine.

Преобразует OHLCV в объяснимый числовой «язык графика» для ML:
- свечные паттерны и геометрия свечи;
- поддержки/сопротивления и pivot-уровни;
- трендовые линии и регрессионные каналы;
- HH/HL/LH/LL market structure;
- breakouts/retests и положение внутри диапазона;
- Fibonacci retracement;
- VWAP/volume/OBV;
- RSI/MACD-like/Stochastic/CCI/ATR/ADX/Bollinger;
- multi-timeframe context, если он передан вызывающим кодом.

Все признаки построены только по данным ДО текущего бара.
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np

from ..core import models
from ..core.utils import calculate_atr, calculate_bollinger_bands, calculate_rsi, exponential_moving_average


def _arr(candles: list[models.Candle]):
    o = np.array([float(c.open) for c in candles], dtype=float)
    h = np.array([float(c.high) for c in candles], dtype=float)
    l = np.array([float(c.low) for c in candles], dtype=float)
    c = np.array([float(c.close) for c in candles], dtype=float)
    v = np.array([float(c.volume) for c in candles], dtype=float)
    return o, h, l, c, v


def _safe(value: float, default: float = 0.0) -> float:
    return default if not math.isfinite(value) else float(value)


def _linreg(values: np.ndarray, window: int) -> tuple[float, float]:
    if len(values) < window:
        return 0.0, 0.0
    y = values[-window:]
    x = np.arange(window, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    scale = max(abs(float(y[-1])), 1e-12)
    return _safe(slope / scale * window), _safe(r2)


def _ema(values: np.ndarray, period: int) -> float:
    if len(values) < period:
        return float(values[-1]) if len(values) else 0.0
    return float(exponential_moving_average(values[-period:].tolist(), period))


def _stochastic(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
    if len(close) < period:
        return 50.0
    hh = float(np.max(high[-period:]))
    ll = float(np.min(low[-period:]))
    return 50.0 if hh <= ll else _safe((close[-1] - ll) / (hh - ll) * 100.0, 50.0)


def _cci(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 20) -> float:
    if len(close) < period:
        return 0.0
    tp = (high + low + close) / 3.0
    window = tp[-period:]
    mean = float(np.mean(window))
    mad = float(np.mean(np.abs(window - mean)))
    return 0.0 if mad <= 1e-12 else _safe((tp[-1] - mean) / (0.015 * mad))


def _obv(close: np.ndarray, volume: np.ndarray) -> float:
    if len(close) < 2:
        return 0.0
    direction = np.sign(np.diff(close))
    obv = np.concatenate([[0.0], np.cumsum(direction * volume[1:])])
    base = max(float(np.mean(volume[-20:])) if len(volume) >= 20 else 1.0, 1e-9)
    return _safe((obv[-1] - obv[max(0, len(obv) - 21)]) / base)


def _pivot_points(high: np.ndarray, low: np.ndarray, left: int = 2, right: int = 2):
    highs: list[int] = []
    lows: list[int] = []
    n = len(high)
    for i in range(left, n - right):
        if high[i] >= np.max(high[i-left:i]) and high[i] > np.max(high[i+1:i+right+1]):
            highs.append(i)
        if low[i] <= np.min(low[i-left:i]) and low[i] < np.min(low[i+1:i+right+1]):
            lows.append(i)
    return highs, lows


def _structure_features(high: np.ndarray, low: np.ndarray, close: np.ndarray, atr: float) -> dict[str, float]:
    ph, pl = _pivot_points(high, low)
    out = {
        "structure_hh": 0.0, "structure_hl": 0.0,
        "structure_lh": 0.0, "structure_ll": 0.0,
        "structure_bias": 0.0, "breakout_up": 0.0, "breakout_down": 0.0,
        "retest_support": 0.0, "retest_resistance": 0.0,
        "pivot_high_distance_atr": 0.0, "pivot_low_distance_atr": 0.0,
    }
    if len(ph) >= 2:
        a, b = ph[-2], ph[-1]
        out["structure_hh"] = float(high[b] > high[a])
        out["structure_lh"] = float(high[b] <= high[a])
    if len(pl) >= 2:
        a, b = pl[-2], pl[-1]
        out["structure_hl"] = float(low[b] > low[a])
        out["structure_ll"] = float(low[b] <= low[a])

    out["structure_bias"] = (
        out["structure_hh"] + out["structure_hl"]
        - out["structure_lh"] - out["structure_ll"]
    ) / 2.0

    last = float(close[-1])
    tol = max(atr * 0.25, last * 0.001)
    if ph:
        resistance = float(high[ph[-1]])
        out["pivot_high_distance_atr"] = _safe((resistance - last) / max(atr, 1e-9))
        if last > resistance + tol:
            out["breakout_up"] = 1.0
        elif abs(last - resistance) <= tol:
            out["retest_resistance"] = 1.0
    if pl:
        support = float(low[pl[-1]])
        out["pivot_low_distance_atr"] = _safe((last - support) / max(atr, 1e-9))
        if last < support - tol:
            out["breakout_down"] = 1.0
        elif abs(last - support) <= tol:
            out["retest_support"] = 1.0
    return out


def _chart_level_features(high: np.ndarray, low: np.ndarray, close: np.ndarray, atr: float) -> dict[str, float]:
    last = float(close[-1])
    out: dict[str, float] = {}
    for window in (20, 50, 100, 200):
        if len(close) < window:
            continue
        hi = float(np.max(high[-window:]))
        lo = float(np.min(low[-window:]))
        span = max(hi - lo, 1e-9)
        out[f"range_position_{window}"] = _safe((last - lo) / span)
        out[f"distance_resistance_{window}_atr"] = _safe((hi - last) / max(atr, 1e-9))
        out[f"distance_support_{window}_atr"] = _safe((last - lo) / max(atr, 1e-9))
        out[f"range_width_{window}_atr"] = _safe(span / max(atr, 1e-9))
        # Horizontal level density: how often recent bars revisited the edges.
        edge = max(span * 0.02, atr * 0.35)
        near_hi = np.mean(np.abs(high[-window:] - hi) <= edge)
        near_lo = np.mean(np.abs(low[-window:] - lo) <= edge)
        out[f"resistance_touches_{window}"] = _safe(float(near_hi))
        out[f"support_touches_{window}"] = _safe(float(near_lo))
    return out


def _trend_channel_features(close: np.ndarray, atr: float) -> dict[str, float]:
    out: dict[str, float] = {}
    for window in (20, 50, 100, 200):
        if len(close) < window:
            continue
        slope, r2 = _linreg(close, window)
        y = close[-window:]
        x = np.arange(window, dtype=float)
        raw_slope, intercept = np.polyfit(x, y, 1)
        fitted = raw_slope * x + intercept
        resid_std = float(np.std(y - fitted))
        channel_half = max(resid_std * 2.0, atr * 0.5)
        center = float(fitted[-1])
        out[f"trend_slope_{window}"] = slope
        out[f"trend_r2_{window}"] = r2
        out[f"channel_position_{window}"] = _safe((close[-1] - center) / channel_half)
        out[f"channel_width_{window}_atr"] = _safe((2.0 * channel_half) / max(atr, 1e-9))
        out[f"channel_breakout_up_{window}"] = float(close[-1] > center + channel_half)
        out[f"channel_breakout_down_{window}"] = float(close[-1] < center - channel_half)
    return out


def _fibonacci_features(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> dict[str, float]:
    out: dict[str, float] = {}
    if len(close) < 50:
        return out
    hi_i = int(np.argmax(high[-100:]))
    lo_i = int(np.argmin(low[-100:]))
    hi = float(np.max(high[-100:]))
    lo = float(np.min(low[-100:]))
    if hi <= lo:
        return out
    span = hi - lo
    levels = (0.236, 0.382, 0.5, 0.618, 0.786)
    if hi_i > lo_i:
        # Последнее движение low -> high, retrace измеряется от high вниз.
        for level in levels:
            price = hi - span * level
            out[f"fib_{str(level).replace('.', '_')}"] = _safe((close[-1] - price) / span)
    else:
        for level in levels:
            price = lo + span * level
            out[f"fib_{str(level).replace('.', '_')}"] = _safe((close[-1] - price) / span)
    return out


def _candle_features(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> dict[str, float]:
    out: dict[str, float] = {}
    if len(close) < 3:
        return out
    o, h, l, c = map(float, (open_[-1], high[-1], low[-1], close[-1]))
    po, ph, pl, pc = map(float, (open_[-2], high[-2], low[-2], close[-2]))
    rng = max(h - l, 1e-9)
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    prev_body = abs(pc - po)

    out.update({
        "candle_body_pct": _safe(body / rng),
        "candle_upper_wick_pct": _safe(upper / rng),
        "candle_lower_wick_pct": _safe(lower / rng),
        "candle_close_location": _safe((c - l) / rng),
        "candle_bull": float(c > o),
        "candle_bear": float(c < o),
        "candle_doji": float(body <= rng * 0.10),
        "candle_hammer": float(lower >= body * 2.0 and upper <= body * 0.75),
        "candle_shooting_star": float(upper >= body * 2.0 and lower <= body * 0.75),
        "candle_inside_bar": float(h <= ph and l >= pl),
        "candle_range_vs_prev": _safe(rng / max(ph - pl, 1e-9)),
    })
    # Engulfing patterns.
    out["bullish_engulfing"] = float(pc < po and c > o and o <= pc and c >= po and body > prev_body)
    out["bearish_engulfing"] = float(pc > po and c < o and o >= pc and c <= po and body > prev_body)

    # Three-bar patterns.
    if len(close) >= 3:
        oo, hh, ll, cc = map(float, (open_[-3], high[-3], low[-3], close[-3]))
        mid1 = (oo + cc) / 2.0
        out["morning_star"] = float(cc < oo and close[-2] > open_[-2] and c > mid1)
        out["evening_star"] = float(cc > oo and close[-2] < open_[-2] and c < mid1)
    return out


def _indicator_features(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, atr: float) -> dict[str, float]:
    out: dict[str, float] = {}
    rsi = calculate_rsi(close.tolist(), period=14) or 50.0
    out["rsi_14"] = _safe(rsi, 50.0)
    out["stochastic_14"] = _stochastic(high, low, close, 14)
    out["cci_20"] = _cci(high, low, close, 20)
    out["atr_pct"] = _safe(atr / max(close[-1], 1e-9) * 100.0)

    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    ema50 = _ema(close, 50)
    ema200 = _ema(close, 200)
    macd = ema12 - ema26
    signal_proxy = _ema(close, 9)
    out["macd_pct"] = _safe(macd / max(close[-1], 1e-9) * 100.0)
    out["macd_hist_proxy_pct"] = _safe((macd - (ema12 - signal_proxy)) / max(close[-1], 1e-9) * 100.0)
    out["ema20_gap_pct"] = _safe((close[-1] - _ema(close, 20)) / max(close[-1], 1e-9) * 100.0)
    out["ema50_gap_pct"] = _safe((close[-1] - ema50) / max(close[-1], 1e-9) * 100.0)
    out["ema200_gap_pct"] = _safe((close[-1] - ema200) / max(close[-1], 1e-9) * 100.0)
    out["ema_stack_bull"] = float(ema12 > ema26 > ema50 > ema200)
    out["ema_stack_bear"] = float(ema12 < ema26 < ema50 < ema200)

    bb = calculate_bollinger_bands(close.tolist(), 20, 2.0)
    if bb:
        upper, middle, lower = bb["upper"], bb["middle"], bb["lower"]
        width = max(float(upper - lower), 1e-9)
        out["bb_position"] = _safe((close[-1] - lower) / width)
        out["bb_width_pct"] = _safe(width / max(close[-1], 1e-9) * 100.0)
        out["bb_squeeze"] = float(out["bb_width_pct"] < 2.0)

    # ADX-like directional strength.
    if len(close) >= 15:
        tr = np.maximum.reduce([high[1:] - low[1:], abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1])])
        plus_dm = np.maximum(high[1:] - high[:-1], 0.0)
        minus_dm = np.maximum(low[:-1] - low[1:], 0.0)
        tr_sum = float(np.sum(tr[-14:]))
        if tr_sum > 0:
            pdi = float(np.sum(plus_dm[-14:]) / tr_sum * 100.0)
            mdi = float(np.sum(minus_dm[-14:]) / tr_sum * 100.0)
            dx = abs(pdi - mdi) / max(pdi + mdi, 1e-9) * 100.0
            out["plus_di"] = _safe(pdi)
            out["minus_di"] = _safe(mdi)
            out["adx_proxy"] = _safe(dx)
            out["trend_direction"] = 1.0 if pdi > mdi else -1.0 if mdi > pdi else 0.0

    if len(volume) >= 20:
        avg20 = float(np.mean(volume[-20:]))
        std20 = float(np.std(volume[-20:]))
        out["volume_ratio_20"] = _safe(volume[-1] / max(avg20, 1e-9))
        out["volume_zscore_20"] = _safe((volume[-1] - avg20) / max(std20, 1e-9))
        out["obv_slope"] = _obv(close, volume)
        vwap_num = float(np.sum(close[-20:] * volume[-20:]))
        vwap_den = float(np.sum(volume[-20:]))
        if vwap_den > 0:
            vwap = vwap_num / vwap_den
            out["vwap_distance_pct"] = _safe((close[-1] - vwap) / max(vwap, 1e-9) * 100.0)

    return out


def compute_market_features(
    candles: list[models.Candle],
    *,
    timeframe: str = "1h",
    extra_features: dict[str, float] | None = None,
) -> dict[str, float]:
    """Построить единый market/chart feature vector.

    Требует минимум 60 баров; для EMA200 и дальних уровней полезно >= 200.
    """
    if len(candles) < 60:
        return {}
    o, h, l, c, v = _arr(candles)
    atr = calculate_atr(h.tolist(), l.tolist(), c.tolist(), period=14) or 0.0
    out: dict[str, float] = {
        "timeframe_15m": float(timeframe == "15m"),
        "timeframe_1h": float(timeframe == "1h"),
        "timeframe_4h": float(timeframe == "4h"),
        "timeframe_1d": float(timeframe == "1d"),
    }
    out.update(_candle_features(o, h, l, c))
    out.update(_indicator_features(h, l, c, v, float(atr)))
    out.update(_chart_level_features(h, l, c, float(atr)))
    out.update(_trend_channel_features(c, float(atr)))
    out.update(_structure_features(h, l, c, float(atr)))
    out.update(_fibonacci_features(h, l, c))

    # Fractal/impulse context.
    if len(c) >= 30:
        ret1 = c[-1] / c[-2] - 1.0 if c[-2] else 0.0
        ret5 = c[-1] / c[-6] - 1.0 if c[-6] else 0.0
        ret20 = c[-1] / c[-21] - 1.0 if c[-21] else 0.0
        out["return_1"] = _safe(ret1)
        out["return_5"] = _safe(ret5)
        out["return_20"] = _safe(ret20)
        out["impulse_score"] = _safe(ret5 / max(float(atr) / max(c[-1], 1e-9), 1e-9))

    if extra_features:
        out.update({k: _safe(float(v)) for k, v in extra_features.items()})
    return out
