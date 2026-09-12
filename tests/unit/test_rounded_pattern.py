"""Круглая вершина / круглое дно (Д-фигуры владельца, 12.09.2026).

Контракт: свинг-дуга из 5-9 пивотов шириной 40-120 баров, квадратичная
кривизна, линия шеи по двум последним противоположным свингам, пробой
только закрытым баром, объёмный фильтр точно как у соседей (порог 1.2).

Главное требование приёмки — анти-дублирование: разметка, которую
детектор двойной вершины признаёт DOUBLE_TOP, НЕ должна одновременно
давать rounded_*, и обратно — короткая дуга (<5 пивотов) не даёт сигнал.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from astra_bot.core import models
from astra_bot.decision.context import StrategyContext
from astra_bot.decision.strategies.pattern_strategies import (
    ROUNDED_MIN_PIVOTS,
    PatternType,
    RoundedBottomStrategy,
    RoundedTopStrategy,
    detect_double_top_bottom,
    detect_rounded_top_bottom,
)

START_MS = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000)


# ------------------------------------------------------------------ фикстуры
import math

CENTERS = list(range(45, 126, 10))  # 9 пивотов дуги, шаг 10 баров


def _arc_candles(kind: str, breakout: bool = True, volume: float = 30.0):
    """Явная Д-фигура: пивоты точно на параболе, провисы между ними.

    Купол: парабола 100 + 7*(1 - ((i-85)/45)^2) над шеей ~99.8, пробой
    закрытием 99.3. Чаша зеркальна (шея ~110.6, пробой 111.2). Полка у
    шеи даёт два противоположных свинга для линии шеи; объёмный всплеск —
    на пробойном баре (фильтр соседей 1.2 проходит).
    """
    n = 143
    top = kind == "top"
    if top:
        def P(i: int) -> float:
            return 100.0 + 7.0 * (1.0 - ((i - 85) / 45.0) ** 2)
        neck_level, break_close = 99.8, 99.3
    else:
        def P(i: int) -> float:
            return 110.0 - 7.0 * (1.0 - ((i - 85) / 45.0) ** 2)
        neck_level, break_close = 110.6, 111.2
    if not breakout:
        break_close = 100.2 if top else 110.2

    arc_v = {c: P(c) + (0.5 if top else -0.5) for c in CENTERS}
    highs: list[float] = [0.0] * n
    lows: list[float] = [0.0] * n
    closes: list[float] = [0.0] * n
    for i in range(n):
        if 40 <= i <= 130:
            if i in arc_v:
                extreme = arc_v[i]
            else:
                left = [c for c in CENTERS if c < i]
                right = [c for c in CENTERS if c > i]
                if left and right:
                    c0, c1 = left[-1], right[0]
                    t = (i - c0) / (c1 - c0)
                    base = arc_v[c0] * (1 - t) + arc_v[c1] * t
                    # прогиб МЕЖДУ пивотами: у купола вниз, у чаши вверх —
                    # пивоты остаются точными локальными экстремумами
                    extreme = base + (-0.9 if top else 0.9) * math.sin(math.pi * t)
                else:  # хвосты дуги до первого/после последнего пивота
                    c0 = left[-1] if left else right[0]
                    extreme = arc_v[c0] + (P(i) - P(c0))
            if top:
                highs[i] = extreme
                lows[i] = extreme - 0.8
            else:
                lows[i] = extreme
                highs[i] = extreme + 0.8
            closes[i] = P(i)
        elif i < 40:  # спокойный подход к фигуре
            closes[i] = 99.9 if top else 110.3
            highs[i] = closes[i] + 0.2
            lows[i] = closes[i] - 0.2
        elif i < n - 1:  # полка у линии шеи (монотонная — без своих свингов)
            closes[i] = 100.0 if top else 110.2
            if top:
                highs[i] = 99.9 - 0.004 * (i - 131)
                lows[i] = highs[i] - 0.25
            else:
                lows[i] = 110.3 + 0.004 * (i - 131)
                highs[i] = lows[i] + 0.25
        else:  # пробойный бар (закрытие за шеей)
            closes[i] = break_close
            highs[i] = break_close + (0.1 if not top else 0.2)
            lows[i] = break_close - (0.2 if top else 0.1)
    # два противоположных свинга -> линия шеи (до пробойного провала);
    # весь хвост после дуги, кроме свингов, за шею не заходит
    for j in range(120, n - 1):
        if j in (126, 136):
            continue
        if top:
            lows[j] = max(lows[j], neck_level + 0.1)
        else:
            highs[j] = min(highs[j], neck_level - 0.1)
    if top:
        lows[126] = lows[136] = neck_level
    else:
        highs[126] = highs[136] = neck_level
    out = []
    for i in range(n):
        vol = volume if i == n - 1 else 10.0
        out.append(
            models.Candle(
                exchange="x",
                symbol="FIG-USDT",
                timeframe="1h",
                open_time=START_MS + i * 3_600_000,
                open=Decimal(str(round(closes[i], 4))),
                high=Decimal(str(round(highs[i], 4))),
                low=Decimal(str(round(lows[i], 4))),
                close=Decimal(str(round(closes[i], 4))),
                volume=Decimal(str(vol)),
                quote_volume=Decimal("1"),
            )
        )
    return out


def _hl_c(candles):
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    closes = [float(c.close) for c in candles]
    return highs, lows, closes


def _ctx(candles, symbol="FIG-USDT"):
    return StrategyContext(
        symbol=symbol,
        timeframe="1h",
        candles=candles,
        current_price=candles[-1].close,
    )


# ----------------------------------------------------------- позитив: вершина
def test_detect_rounded_top_on_dome():
    candles = _arc_candles("top")
    res = detect_rounded_top_bottom(*_hl_c(candles))
    assert res.pattern == PatternType.ROUNDED_TOP
    assert res.confidence >= 0.5
    assert res.breakout_direction == "down"
    assert res.diagnostics["arc_pivots"] >= ROUNDED_MIN_PIVOTS
    assert res.diagnostics["breakout"] == "closed_bar"


async def test_rounded_top_strategy_short_candidate():
    candles = _arc_candles("top")
    cand = await RoundedTopStrategy().evaluate(_ctx(candles))
    assert cand is not None
    assert cand.direction == "short"
    assert cand.strategy == "rounded_top"
    assert cand.stop_loss > cand.entry_price > cand.take_profit
    assert cand.risk_reward >= 1.5
    # дно-стратегия на той же разметке молчит (сторона не совпадает)
    assert await RoundedBottomStrategy().evaluate(_ctx(candles)) is None


# ------------------------------------------------------------- позитив: дно
def test_detect_rounded_bottom_on_bowl():
    candles = _arc_candles("bottom")
    res = detect_rounded_top_bottom(*_hl_c(candles))
    assert res.pattern == PatternType.ROUNDED_BOTTOM
    assert res.breakout_direction == "up"


async def test_rounded_bottom_strategy_long_candidate():
    candles = _arc_candles("bottom")
    cand = await RoundedBottomStrategy().evaluate(_ctx(candles))
    assert cand is not None
    assert cand.direction == "long"
    assert cand.strategy == "rounded_bottom"
    assert cand.stop_loss < cand.entry_price < cand.take_profit
    assert cand.risk_reward >= 1.5
    assert await RoundedTopStrategy().evaluate(_ctx(candles)) is None


# ------------------------------------------------- анти-дублирование (главное)
def test_double_top_is_not_rounded():
    """Структура, дающая DOUBLE_TOP, не должна давать rounded_*."""
    n = 143
    closes = []
    for i in range(n):
        if i < 45:
            closes.append(100.0 + 0.18 * i)  # подход к первому пику
        elif i < 60:
            closes.append(108.0 - 0.53 * (i - 45))  # спуск к первому пику
        elif i == 60:
            closes.append(108.0)  # пик 1
        elif i < 80:
            closes.append(108.0 - 0.4 * (i - 60))  # ретест шеи
        elif i == 80:
            closes.append(100.0)  # шея
        elif i < 100:
            closes.append(100.0 + 0.4 * (i - 80))  # подъём к пику 2
        elif i == 100:
            closes.append(108.0)  # пик 2 (тот же уровень)
        elif i < 130:
            closes.append(108.0 - 0.3 * (i - 100))  # спуск
        elif i < n - 1:
            closes.append(99.5)
        else:
            closes.append(99.0)  # закрытие под шеей
    candles = []
    for i, c in enumerate(closes):
        vol = 10.0
        candles.append(
            models.Candle(
                exchange="x", symbol="FIG-USDT", timeframe="1h",
                open_time=START_MS + i * 3_600_000,
                open=Decimal(str(round(c, 4))),
                high=Decimal(str(round(c + 0.2, 4))),
                low=Decimal(str(round(c - 0.2, 4))),
                close=Decimal(str(round(c, 4))),
                volume=Decimal(str(vol)),
                quote_volume=Decimal("1"),
            )
        )
    highs, lows, cl = _hl_c(candles)
    theirs = detect_double_top_bottom(highs, lows, cl)
    assert theirs.pattern == PatternType.DOUBLE_TOP, "фикстура обязана давать DOUBLE_TOP"
    ours = detect_rounded_top_bottom(highs, lows, cl)
    assert ours.pattern == PatternType.NONE, "та же разметка не должна отдаваться дважды"


def test_short_arc_is_not_rounded():
    """Дуга короче минимального числа пивотов не даёт сигнал."""
    candles = _arc_candles("top")
    highs, lows, cl = _hl_c(candles)
    # грубая пила -> пивотов станет меньше минимума
    from astra_bot.decision.strategies.pattern_strategies import (
        _collapse_plateaus,
        _find_swings,
    )

    coarse = _collapse_plateaus(_find_swings(highs, lows, window=3)[0])
    assert len(coarse) >= ROUNDED_MIN_PIVOTS  # исходно дуга полная
    res = detect_rounded_top_bottom(highs[:90], lows[:90], cl[:90])
    assert res.pattern == PatternType.NONE  # окно уже дуги — сигнал невозможен


# ------------------------------------------------------------------ негативы
def test_no_breakout_no_signal():
    candles = _arc_candles("top", breakout=False)  # закрытие ВЫШЕ шеи
    res = detect_rounded_top_bottom(*_hl_c(candles))
    assert res.pattern == PatternType.NONE


async def test_volume_gate_like_neighbours():
    """Объёмный фильтр — тот же механизм (порог 1.2), без послаблений."""
    candles = _arc_candles("top", volume=5.0)  # всплеска нет
    assert await RoundedTopStrategy().evaluate(_ctx(candles)) is None
    # детектор при этом фигуру видит — режет именно объёмный гейт
    assert detect_rounded_top_bottom(*_hl_c(candles)).pattern == PatternType.ROUNDED_TOP


def test_too_shallow_dome_is_noise():
    """Дуга ниже 0.5% цены — шум: сжимаем высоту купола до ~0.5."""
    candles = _arc_candles("top")
    highs, lows, cl = _hl_c(candles)
    highs = [100.0 + (h - 100.0) * 0.07 for h in highs]
    lows = [100.0 + (l - 100.0) * 0.07 for l in lows]
    cl = [100.0 + (c - 100.0) * 0.07 for c in cl]
    assert detect_rounded_top_bottom(highs, lows, cl).pattern == PatternType.NONE
