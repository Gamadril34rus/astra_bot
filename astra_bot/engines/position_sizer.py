"""
Position Sizer — Block 6.2: Расчёт размера позиции с Kelly, ML confidence, volatility.

- Базовый: risk_amount / sl_distance
- Корректировка Kelly Criterion (quarter-Kelly, max 25%)
- Корректировка на ML confidence (0.5x — 1.0x)
- Корректировка на волатильность (высокая волатильность → меньше позиция)
- Жёсткий максимум: 10% баланса
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """
    Kelly Criterion: f = W - (1-W)/R, where R = avg_win/|avg_loss|
    Returns fraction (0-1). Quarter-Kelly applied later.
    """
    if avg_loss == 0 or win_rate <= 0:
        return 0.0
    r = abs(avg_win) / abs(avg_loss) if avg_loss != 0 else 0
    if r == 0:
        return 0.0
    kelly = win_rate - (1 - win_rate) / r
    return max(0.0, min(kelly, 1.0))


def calculate_position_size(
    equity: Decimal,
    entry_price: Decimal,
    stop_loss: Decimal,
    risk_per_trade_pct: Decimal = Decimal("0.01"),
    win_rate: float = 0.55,
    avg_win_r: float = 1.5,
    avg_loss_r: float = 1.0,
    ml_confidence: float | None = None,
    atr_pct: float | None = None,
    max_notional_pct: Decimal = Decimal("0.10"),
) -> Decimal:
    """
    Calculate position size with adjustments (Block 6.2).

    Args:
        equity: Current equity
        entry_price: Entry price
        stop_loss: Stop loss price
        risk_per_trade_pct: Base risk per trade (1%)
        win_rate: Historical win rate for Kelly
        avg_win_r: Avg win in R
        avg_loss_r: Avg loss in R
        ml_confidence: ML model confidence 0-1 (0.5x to 1.0x adjustment)
        atr_pct: ATR as % of price for volatility adjustment
        max_notional_pct: Hard max 10% of balance

    Returns:
        Position size (quantity)
    """
    if equity <= 0 or entry_price <= 0:
        return Decimal("0")

    stop_distance = abs(entry_price - stop_loss)
    if stop_distance <= 0:
        # Fallback 1% distance
        stop_distance = abs(entry_price) * Decimal("0.01")
        if stop_distance <= 0:
            stop_distance = Decimal("0.001")

    # Base: risk_amount / sl_distance
    risk_amount = equity * risk_per_trade_pct
    base_size = risk_amount / stop_distance

    # Kelly adjustment (quarter-Kelly, max 25%)
    try:
        kelly = kelly_fraction(win_rate, avg_win_r, avg_loss_r)
        quarter_kelly = kelly * 0.25
        kelly_capped = min(quarter_kelly, 0.25)
        # If Kelly says 0, we still allow base size but with reduction
        if kelly > 0:
            kelly_multiplier = 0.5 + kelly_capped  # 0.5x to 0.75x range, but if kelly high, up to 0.75x
            # Actually quarter-Kelly max 25% means multiplier up to 1.0 if kelly high?
            # Simplify: multiplier = 0.5 + kelly_capped*2 -> 0.5 to 1.0
            kelly_multiplier = 0.5 + min(kelly_capped * 2, 0.5)
        else:
            kelly_multiplier = 0.5  # Conservative if no edge
    except Exception:
        kelly_multiplier = 1.0

    size = base_size * Decimal(str(kelly_multiplier))

    # ML confidence adjustment (0.5x - 1.0x)
    if ml_confidence is not None:
        try:
            conf = float(ml_confidence)
            # Map 0.0-1.0 to 0.5-1.0
            conf = max(0.0, min(1.0, conf))
            ml_multiplier = 0.5 + conf * 0.5
            size = size * Decimal(str(ml_multiplier))
        except Exception:
            pass

    # Volatility adjustment (high vol -> smaller position)
    if atr_pct is not None:
        try:
            atr = float(atr_pct)
            # If ATR > 2%, reduce size
            if atr > 2.0:
                vol_multiplier = max(0.3, 2.0 / atr)
                size = size * Decimal(str(vol_multiplier))
            elif atr > 5.0:
                # Extreme vol -> 0.3x
                size = size * Decimal("0.3")
        except Exception:
            pass

    # Hard max: 10% balance
    max_notional = equity * max_notional_pct
    max_size_by_notional = max_notional / entry_price
    if size > max_size_by_notional:
        size = max_size_by_notional

    # Ensure size is positive and quantized
    if size <= 0:
        return Decimal("0")

    return size.quantize(Decimal("0.000001"))


def calculate_sl_tp(
    entry_price: Decimal,
    direction: str,
    atr: Decimal | None = None,
    swing_low: Decimal | None = None,
    swing_high: Decimal | None = None,
    rr_min: float = 2.0,
) -> tuple[Decimal, Decimal]:
    """
    Calculate SL/TP per Block 6.3:
    - SL: behind nearest swing or ATR-based (1.5*ATR)
    - TP: min 2:1 R:R
    """
    if direction.lower() in ("long", "buy"):
        if swing_low is not None and swing_low < entry_price:
            sl = swing_low
        elif atr is not None:
            sl = entry_price - atr * Decimal("1.5")
        else:
            sl = entry_price * Decimal("0.99")  # 1% SL fallback

        risk = entry_price - sl
        if risk <= 0:
            risk = entry_price * Decimal("0.01")
            sl = entry_price - risk

        tp = entry_price + risk * Decimal(str(rr_min))
    else:
        if swing_high is not None and swing_high > entry_price:
            sl = swing_high
        elif atr is not None:
            sl = entry_price + atr * Decimal("1.5")
        else:
            sl = entry_price * Decimal("1.01")

        risk = sl - entry_price
        if risk <= 0:
            risk = entry_price * Decimal("0.01")
            sl = entry_price + risk

        tp = entry_price - risk * Decimal(str(rr_min))

    return sl, tp
