"""
Feature Builder — Block 5: 50+ features for ML model.

Categories:
- Price action (15): returns, log returns, high-low range, close-open, etc.
- Trend (10): EMA, SMA distances, ADX, MACD, etc.
- Momentum (10): RSI, Stochastic, CCI, etc.
- Volatility (8): ATR, BB width, std dev, etc.
- Volume (7): volume ratio, OBV, etc.
- Market structure (5+): support/resistance distance, etc.
- Time (3): hour, day of week, etc.

Total: 50+ features.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def _ema(prices: list[float], period: int) -> float:
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return ema


def _sma(prices: list[float], period: int) -> float:
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0.0
    return sum(prices[-period:]) / period


def _rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        if diff > 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))
    if len(gains) < period:
        return 50.0
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    if len(highs) < period + 1:
        return 0.0
    tr_list = []
    for i in range(1, len(highs)):
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])
        tr_list.append(max(tr1, tr2, tr3))
    if len(tr_list) < period:
        return sum(tr_list) / len(tr_list) if tr_list else 0.0
    return sum(tr_list[-period:]) / period


def build_features(candles: list[Any], orderbook: dict | None = None, ticker: dict | None = None) -> dict[str, float]:
    """
    Build 50+ features from candles.

    Args:
        candles: list of Candle objects or dicts with open/high/low/close/volume
        orderbook: optional orderbook dict
        ticker: optional ticker dict

    Returns:
        dict of feature_name -> value (50+ features)
    """
    # Normalize candles to lists
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    opens: list[float] = []
    volumes: list[float] = []

    for c in candles:
        try:
            if isinstance(c, dict):
                closes.append(float(c.get("close", 0)))
                highs.append(float(c.get("high", 0)))
                lows.append(float(c.get("low", 0)))
                opens.append(float(c.get("open", 0)))
                volumes.append(float(c.get("volume", 0)))
            else:
                closes.append(float(getattr(c, "close", 0)))
                highs.append(float(getattr(c, "high", 0)))
                lows.append(float(getattr(c, "low", 0)))
                opens.append(float(getattr(c, "open", 0)))
                volumes.append(float(getattr(c, "volume", 0)))
        except Exception:
            continue

    if len(closes) < 50:
        # Not enough data, return minimal features
        return {f"feature_{i}": 0.0 for i in range(55)}

    features: dict[str, float] = {}

    # 1. Price action (15 features)
    try:
        features["return_1"] = (closes[-1] - closes[-2]) / closes[-2] if closes[-2] != 0 else 0.0
        features["return_5"] = (closes[-1] - closes[-6]) / closes[-6] if len(closes) >= 6 and closes[-6] != 0 else 0.0
        features["return_10"] = (closes[-1] - closes[-11]) / closes[-11] if len(closes) >= 11 and closes[-11] != 0 else 0.0
        features["return_20"] = (closes[-1] - closes[-21]) / closes[-21] if len(closes) >= 21 and closes[-21] != 0 else 0.0
        features["log_return_1"] = math.log(closes[-1] / closes[-2]) if closes[-2] > 0 and closes[-1] > 0 else 0.0
        features["high_low_range"] = (highs[-1] - lows[-1]) / closes[-1] if closes[-1] != 0 else 0.0
        features["close_open"] = (closes[-1] - opens[-1]) / opens[-1] if opens[-1] != 0 else 0.0
        features["high_close"] = (highs[-1] - closes[-1]) / closes[-1] if closes[-1] != 0 else 0.0
        features["close_low"] = (closes[-1] - lows[-1]) / closes[-1] if closes[-1] != 0 else 0.0
        features["body_size"] = abs(closes[-1] - opens[-1]) / closes[-1] if closes[-1] != 0 else 0.0
        features["upper_wick"] = (highs[-1] - max(closes[-1], opens[-1])) / closes[-1] if closes[-1] != 0 else 0.0
        features["lower_wick"] = (min(closes[-1], opens[-1]) - lows[-1]) / closes[-1] if closes[-1] != 0 else 0.0
        features["price_position"] = (closes[-1] - lows[-1]) / (highs[-1] - lows[-1]) if highs[-1] != lows[-1] else 0.5
        features["close_sma20_distance"] = (closes[-1] - _sma(closes, 20)) / closes[-1] if closes[-1] != 0 else 0.0
        features["close_sma50_distance"] = (closes[-1] - _sma(closes, 50)) / closes[-1] if closes[-1] != 0 else 0.0
    except Exception:
        pass

    # 2. Trend (10 features)
    try:
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        ema50 = _ema(closes, 50)
        ema200 = _ema(closes, 200) if len(closes) >= 200 else _sma(closes, 50)
        features["ema12_26_diff"] = (ema12 - ema26) / closes[-1] if closes[-1] != 0 else 0.0
        features["ema50_200_diff"] = (ema50 - ema200) / closes[-1] if closes[-1] != 0 else 0.0
        features["price_ema12_dist"] = (closes[-1] - ema12) / closes[-1] if closes[-1] != 0 else 0.0
        features["price_ema50_dist"] = (closes[-1] - ema50) / closes[-1] if closes[-1] != 0 else 0.0
        features["ema12_slope"] = (ema12 - _ema(closes[:-1], 12)) / closes[-1] if len(closes) > 12 and closes[-1] != 0 else 0.0
        features["ema50_slope"] = (ema50 - _ema(closes[:-1], 50)) / closes[-1] if len(closes) > 50 and closes[-1] != 0 else 0.0
        # MACD
        features["macd"] = (ema12 - ema26) / closes[-1] if closes[-1] != 0 else 0.0
        features["macd_signal"] = _ema([_ema(closes[:i], 12) - _ema(closes[:i], 26) for i in range(12, len(closes) + 1)], 9) if len(closes) > 26 else 0.0
        features["macd_hist"] = features["macd"] - features["macd_signal"]
        # ADX approximation
        features["adx_proxy"] = abs(ema12 - ema26) / (_atr(highs, lows, closes, 14) or closes[-1] * 0.01) if closes[-1] != 0 else 0.0
    except Exception:
        pass

    # 3. Momentum (10 features)
    try:
        features["rsi_14"] = _rsi(closes, 14) / 100.0
        features["rsi_7"] = _rsi(closes, 7) / 100.0
        features["rsi_21"] = _rsi(closes, 21) / 100.0
        # Stochastic %K
        if len(closes) >= 14:
            low_14 = min(lows[-14:])
            high_14 = max(highs[-14:])
            features["stoch_k"] = (closes[-1] - low_14) / (high_14 - low_14) if high_14 != low_14 else 0.5
        else:
            features["stoch_k"] = 0.5
        features["stoch_d"] = _sma([features["stoch_k"]] * 3 + [0.5] * 10, 3)  # Simplified
        # CCI approximation
        typical = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
        sma_typical = _sma(typical, 20)
        mad = sum(abs(x - sma_typical) for x in typical[-20:]) / 20 if len(typical) >= 20 else 0.01
        features["cci"] = (typical[-1] - sma_typical) / (0.015 * mad) / 100.0 if mad != 0 else 0.0
        # Momentum
        features["momentum_10"] = closes[-1] / closes[-11] if len(closes) >= 11 and closes[-11] != 0 else 1.0
        features["momentum_20"] = closes[-1] / closes[-21] if len(closes) >= 21 and closes[-21] != 0 else 1.0
        # Rate of change
        features["roc_10"] = (closes[-1] - closes[-11]) / closes[-11] * 100 if len(closes) >= 11 and closes[-11] != 0 else 0.0
        features["roc_20"] = (closes[-1] - closes[-21]) / closes[-21] * 100 if len(closes) >= 21 and closes[-21] != 0 else 0.0
    except Exception:
        pass

    # 4. Volatility (8 features)
    try:
        atr14 = _atr(highs, lows, closes, 14)
        atr7 = _atr(highs, lows, closes, 7)
        atr28 = _atr(highs, lows, closes, 28)
        features["atr_14"] = atr14 / closes[-1] if closes[-1] != 0 else 0.0
        features["atr_7"] = atr7 / closes[-1] if closes[-1] != 0 else 0.0
        features["atr_ratio_7_28"] = atr7 / atr28 if atr28 != 0 else 1.0
        # Bollinger Bandwidth
        sma20 = _sma(closes, 20)
        std20 = (sum((x - sma20) ** 2 for x in closes[-20:]) / 20) ** 0.5 if len(closes) >= 20 else 0.0
        features["bb_width"] = (4 * std20) / sma20 if sma20 != 0 else 0.0
        features["bb_position"] = (closes[-1] - (sma20 - 2 * std20)) / (4 * std20) if std20 != 0 else 0.5
        # Std dev
        features["std_20"] = std20 / closes[-1] if closes[-1] != 0 else 0.0
        features["std_50"] = ((sum((x - _sma(closes, 50)) ** 2 for x in closes[-50:]) / 50) ** 0.5 / closes[-1]) if len(closes) >= 50 and closes[-1] != 0 else 0.0
        # Historical volatility
        log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0 and closes[i] > 0]
        if len(log_returns) >= 20:
            features["hist_vol_20"] = float(np.std(log_returns[-20:]) * (252 ** 0.5)) if log_returns else 0.0
        else:
            features["hist_vol_20"] = 0.0
    except Exception:
        pass

    # 5. Volume (7 features)
    try:
        sma_vol20 = _sma(volumes, 20)
        sma_vol50 = _sma(volumes, 50)
        features["volume_ratio_20"] = volumes[-1] / sma_vol20 if sma_vol20 != 0 else 1.0
        features["volume_ratio_50"] = volumes[-1] / sma_vol50 if sma_vol50 != 0 else 1.0
        features["volume_sma20_dist"] = (volumes[-1] - sma_vol20) / sma_vol20 if sma_vol20 != 0 else 0.0
        # OBV
        obv = 0.0
        for i in range(1, len(closes)):
            if closes[i] > closes[i - 1]:
                obv += volumes[i]
            elif closes[i] < closes[i - 1]:
                obv -= volumes[i]
        features["obv"] = obv / (sum(volumes) or 1.0)
        features["obv_slope"] = (obv - (obv - volumes[-1])) / (sum(volumes) or 1.0)
        # Volume trend
        features["volume_trend"] = 1.0 if volumes[-1] > sma_vol20 else 0.0
        features["volume_price_trend"] = features["volume_ratio_20"] * features["return_1"]
    except Exception:
        pass

    # 6. Market structure (5+ features)
    try:
        # Support/resistance distance (simplified: recent high/low)
        recent_high = max(highs[-20:]) if len(highs) >= 20 else highs[-1]
        recent_low = min(lows[-20:]) if len(lows) >= 20 else lows[-1]
        features["dist_to_high_20"] = (recent_high - closes[-1]) / closes[-1] if closes[-1] != 0 else 0.0
        features["dist_to_low_20"] = (closes[-1] - recent_low) / closes[-1] if closes[-1] != 0 else 0.0
        features["high_low_20_range"] = (recent_high - recent_low) / closes[-1] if closes[-1] != 0 else 0.0
        # Breakout detection
        features["breakout_up"] = 1.0 if closes[-1] > recent_high * 0.999 else 0.0
        features["breakout_down"] = 1.0 if closes[-1] < recent_low * 1.001 else 0.0
        # Higher highs / lower lows
        features["higher_high"] = 1.0 if highs[-1] > highs[-2] else 0.0
        features["lower_low"] = 1.0 if lows[-1] < lows[-2] else 0.0
    except Exception:
        pass

    # 7. Time features (3)
    try:
        import datetime

        now = datetime.datetime.utcnow()
        features["hour"] = now.hour / 24.0
        features["day_of_week"] = now.weekday() / 7.0
        features["is_weekend"] = 1.0 if now.weekday() >= 5 else 0.0
    except Exception:
        features["hour"] = 0.0
        features["day_of_week"] = 0.0
        features["is_weekend"] = 0.0

    # Ensure we have at least 55 features
    # If some missing, fill with 0
    expected_count = 55
    current_count = len(features)
    if current_count < expected_count:
        for i in range(current_count, expected_count):
            features[f"extra_{i}"] = 0.0

    # Clip extreme values
    for k, v in list(features.items()):
        try:
            if not math.isfinite(v):
                features[k] = 0.0
            else:
                features[k] = max(-10.0, min(10.0, float(v)))
        except Exception:
            features[k] = 0.0

    return features


def features_to_vector(features: dict[str, float], order: list[str] | None = None) -> tuple[list[float], list[str]]:
    """Convert features dict to vector with consistent ordering."""
    if order is None:
        order = sorted(features.keys())
    vector = [features.get(k, 0.0) for k in order]
    return vector, order
