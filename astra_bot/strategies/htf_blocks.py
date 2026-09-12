"""Набор индикаторов владельца, пункт 7 (12.09.2026): дневные бары.

Источники входов (все — на ДНЕВНЫХ закрытых барах,
``preferred_timeframe = "1d"``):

* ``ob_swing`` — ретест ЖИВОГО (немитигированного) ордерблока по
  механике «Order Blocks & Breaker Blocks [LuxAlgo]» (Swing Lookback 10);
* ``breaker_block`` — ретест СЛОМАННОГО блока с обратной стороны
  (полярность зоны переворачивается после митигации);
* ``maicross`` — кросс EMA 20/50 (MA Cross; дефолты 20/50 подтверждены
  владельцем 12.09 — если всплывут другие периоды, меняются конфигом).

Направляющие фильтры (лента дневных EMA и «по BTC») живут отдельно —
``decision/htf_gates.py`` — и режут кандидатов, а не сигналы здесь.

Ключевые решения владельца (12.09, поправки к спеке 7.0):

1. ОДНОРАЗОВОСТЬ (без состояния): бот перезапускается каждые ~5 минут,
   зоны восстанавливаются из баров, поэтому сигнал существует ТОЛЬКО
   если ПОСЛЕДНИЙ закрытый бар — ПЕРВОЕ касание зоны (между баром
   формирования зоны и им ни одного касания); тронутая зона
   «отработана» и больше не сигналит никогда. ``maicross`` — только сам
   бар кросса (смена знака разницы EMA на последнем закрытом баре).
2. Зона = свеча пивота ЦЕЛИКОМ (полный диапазон; дефолт Люкса
   ``use_candle_body = false``), митигация — ЗАКРЫТИЕМ за дальней
   границей. Осознанное приближение: Pine-исходник скрипта из песочницы
   недоступен; правила восстановлены по официальному описанию и
   открытому Python-порту (PyIndicators: ``swing_length=10``,
   ``use_body=False``).
3. TP кандидата — СТРУКТУРНАЯ цель (measured move: свинг, который
   образовал блок / импульс после него). Финальную зону тейка держит
   единый механизм ``exit_plan.clamp_take_rr`` (2.0–2.3R) — своей
   «1.6R»-логики здесь нет. Гейт кандидата ``RR >= 1.5`` сохранён.

Антидублирование (задекларировано в спеке 7.0): старая
``OrderBlockStrategy`` (импульс ≥1.5% на 5m) и этот модуль (пивот
10/10 на дневках) не пересекаются структурно: разный таймфрейм, разное
событие-источник, разные бакеты статистики.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from ..core import models
from .base import BaseStrategy, Signal, SignalType, StrategyConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Константы (зафиксированы владельцем 12.09; ради частоты входов не
# ослабляются — частотой управляет килл-свитч по статистике).
# ---------------------------------------------------------------------------

# «Swing Lookback» Люкса: окно пивотов 10 баров влево/вправо; пивот
# подтверждается только через 10 баров — запаздывание как в Pine,
# перерисовки нет.
OB_SWING_LOOKBACK = 10

# «Show Last Bullish/Bearish OB = 3»: у Люкса это лимит отображения,
# у нас — лимит активных зон на сторону (самые свежие).
OB_MAX_ZONES_PER_SIDE = 3

# Буфер стопа за дальней границей зоны.
OB_STOP_BUFFER_PCT = 0.002

# Гейт кандидата: риск/доходность не хуже 1.5 (не ослаблять).
MIN_RR = 1.5

# Объёмный фильтр — тот же механизм и порог, что у паттерн-соседей
# (pattern_strategies._volume_ok).
VOLUME_THRESHOLD = 1.2

# MA Cross: дефолты 20/50 подтверждены владельцем.
MAICROSS_FAST = 20
MAICROSS_SLOW = 50
# Стоп — за ближайший кач 10 закрытых дневных баров.
MAICROSS_STOP_LOOKBACK = 10
# Структурная цель — экстремум последних 90 закрытых дневных баров
# (~3 месяца структуры; на дневках 50 баров часто не дотягиваются до
# ближайшего значимого уровня).
MAICROSS_TARGET_LOOKBACK = 90


# ---------------------------------------------------------------------------
# Свинг-структура (аналог ta.pivothigh/ta.pivotlow(len, len))
# ---------------------------------------------------------------------------


def find_swing_pivots(
    highs: list[float], lows: list[float], lookback: int = OB_SWING_LOOKBACK
) -> tuple[list[int], list[int]]:
    """Индексы подтверждённых пивотов (без перерисовки).

    Пивот-хай на баре ``i``: ``highs[i]`` строго выше ``lookback``
    соседей слева и справа; пивот считается только если правая сторона
    целиком внутри ряда (в Pine подтверждение приходит на ``i+lookback``).
    """
    n = len(highs)
    pivot_highs: list[int] = []
    pivot_lows: list[int] = []
    last_confirmed = n - 1 - lookback
    if last_confirmed < lookback:
        return pivot_highs, pivot_lows
    for i in range(lookback, last_confirmed + 1):
        left = range(i - lookback, i)
        right = range(i + 1, i + lookback + 1)
        h = highs[i]
        if all(h > highs[j] for j in left) and all(h > highs[j] for j in right):
            pivot_highs.append(i)
        low = lows[i]
        if all(low < lows[j] for j in left) and all(low < lows[j] for j in right):
            pivot_lows.append(i)
    return pivot_highs, pivot_lows


@dataclass
class ObZone:
    """Ордерблок (зона = свеча пивота целиком, ``use_body=false``)."""

    side: str  # исходная полярность: "bull" / "bear"
    top: float
    bottom: float
    formed_idx: int


def _merge_overlapping(zones: list[ObZone]) -> list[ObZone]:
    """Слияние перекрывающихся зон одной стороны (правило спеки 7.0).

    Зоны идут в порядке формирования; новая, пересекающаяся с последней
    удержанной, вливается в неё (границы — объединение, бар формирования
    — у более ранней: жизненный цикл начинается с первой).
    """
    merged: list[ObZone] = []
    for z in zones:
        if merged and z.bottom < merged[-1].top and z.top > merged[-1].bottom:
            last = merged[-1]
            last.top = max(last.top, z.top)
            last.bottom = min(last.bottom, z.bottom)
        else:
            merged.append(ObZone(z.side, z.top, z.bottom, z.formed_idx))
    return merged


def build_zones(
    highs: list[float],
    lows: list[float],
    lookback: int = OB_SWING_LOOKBACK,
    max_per_side: int = OB_MAX_ZONES_PER_SIDE,
) -> list[ObZone]:
    """Активные зоны: пивот-лой → бычий блок, пивот-хай → медвежий.

    Держим не более ``max_per_side`` самых СВЕЖИХ зон на сторону
    (у Люкса «3/3» — лимит отображения), затем объединяем перекрытия.
    """
    pivot_highs, pivot_lows = find_swing_pivots(highs, lows, lookback)
    bulls = [ObZone("bull", highs[i], lows[i], i) for i in pivot_lows]
    bears = [ObZone("bear", highs[i], lows[i], i) for i in pivot_highs]
    bulls = sorted(bulls, key=lambda z: z.formed_idx)[-max_per_side:]
    bears = sorted(bears, key=lambda z: z.formed_idx)[-max_per_side:]
    return _merge_overlapping(bulls) + _merge_overlapping(bears)


def walk_zone(
    zone: ObZone,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    lookback: int = OB_SWING_LOOKBACK,
) -> tuple[int | None, int | None, int | None, bool]:
    """Жизненный цикл зоны по ЗАКРЫТЫМ барам после её ПОДТВЕРЖДЕНИЯ.

    Пивот подтверждается через ``lookback`` баров (как в Pine), поэтому
    зона «существует» с бара ``formed_idx + lookback``; касания/сломы
    считаются с ``formed_idx + lookback + 1`` — бары самого импульса от
    пивота до подтверждения касаниями НЕ считаются (иначе импульс из
    зоны мгновенно «касался» бы её).

    Возвращает ``(first_touch, broken_at, breaker_touch, dead)``:

    * ``first_touch`` — первый бар, коснувшийся живой зоны (для бычьей:
      ``low <= top`` без пробоя закрытием; для медвежьей зеркально);
    * ``broken_at`` — бар митигации (закрытие за дальней границей);
    * ``breaker_touch`` — первое касание сломанной зоны с обратной
      стороны (кандидат в сигнал брейкера);
    * ``dead`` — цену вернули за зону после слома (полярность не
      сработала, зона гаснет).
    """
    first_touch: int | None = None
    broken_at: int | None = None
    breaker_touch: int | None = None
    n = len(closes)
    for j in range(zone.formed_idx + lookback + 1, n):
        if broken_at is None:
            mitigated = (zone.side == "bull" and closes[j] < zone.bottom) or (
                zone.side == "bear" and closes[j] > zone.top
            )
            if mitigated:
                broken_at = j
            elif first_touch is None:
                touched = lows[j] <= zone.top if zone.side == "bull" else highs[j] >= zone.bottom
                if touched:
                    first_touch = j
        else:
            if zone.side == "bull" and closes[j] > zone.top:
                return first_touch, broken_at, None, True
            if zone.side == "bear" and closes[j] < zone.bottom:
                return first_touch, broken_at, None, True
            if breaker_touch is None:
                touched = highs[j] >= zone.bottom if zone.side == "bull" else lows[j] <= zone.top
                if touched:
                    breaker_touch = j
    return first_touch, broken_at, breaker_touch, False


def _rr_ok(entry: float, stop: float, take: float) -> bool:
    risk = abs(entry - stop)
    reward = abs(take - entry)
    return risk > 0 and reward / risk >= MIN_RR


def _signal(
    symbol: str,
    name: str,
    direction: models.TradeDirection,
    entry: float,
    stop: float,
    take: float,
    market_regime: str | None,
) -> Signal | None:
    """Единый выходной конверт: стоп/тейк посчитаны, гейт ``RR >= 1.5``."""
    if not _rr_ok(entry, stop, take):
        return None
    return Signal(
        symbol=symbol,
        strategy_name=name,
        signal_type=SignalType.MEAN_REVERSION,
        direction=direction,
        entry_price=Decimal(str(round(entry, 10))),
        stop_loss=Decimal(str(round(stop, 10))),
        take_profit=Decimal(str(round(take, 10))),
        confidence=0.6,
        market_regime=market_regime or "UNKNOWN",
        features={"rr": abs(take - entry) / abs(entry - stop)},
    )


def _volume_ok_daily(candles: list[models.Candle]) -> bool:
    """Тот же механизм/порог, что у паттерн-соседей (без послаблений)."""
    from ..decision.strategies.pattern_strategies import _volume_ok

    return _volume_ok([float(c.volume) for c in candles], VOLUME_THRESHOLD)


# ---------------------------------------------------------------------------
# 7.1 ob_swing — ретест живого ордерблока
# ---------------------------------------------------------------------------


@dataclass
class ObSwingConfig(StrategyConfig):
    name: str = "ob_swing"
    enabled: bool = True
    swing_lookback: int = OB_SWING_LOOKBACK
    max_zones_per_side: int = OB_MAX_ZONES_PER_SIDE
    stop_buffer_pct: float = OB_STOP_BUFFER_PCT


class ObSwingStrategy(BaseStrategy[ObSwingConfig]):
    """LuxAlgo-ордерблоки на дневках: вход на ПЕРВОМ касании живой зоны.

    ОДНОРАЗОВОСТЬ (решение владельца): сигнал существует только если
    последний закрытый бар — первое касание зоны; между баром
    формирования и им касаний не было. Зона после касания «отработана».
    """

    # Явный дневной ряд (поправка 2 владельца): пайплайн подаёт
    # ``ctx.candles_on("1d")`` (pipeline.py: preferred_timeframe).
    preferred_timeframe = "1d"

    def __init__(self, config: ObSwingConfig | None = None) -> None:
        super().__init__(config or ObSwingConfig())
        self.config: ObSwingConfig

    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook=None,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Signal | None:
        c = self.config
        if not c.enabled:
            return None
        try:
            # Пайплайн уже отсёк формирующийся бар (А5): здесь только
            # ЗАКРЫТЫЕ дневные свечи; последняя = последний закрытый бар.
            need = 2 * c.swing_lookback + 5
            if len(candles) < need:
                return None
            highs = [float(x.high) for x in candles]
            lows = [float(x.low) for x in candles]
            closes = [float(x.close) for x in candles]
            last = len(candles) - 1
            price = float(current_price or closes[-1])
            for zone in build_zones(highs, lows, c.swing_lookback, c.max_zones_per_side):
                first_touch, _broken, _btouch, dead = walk_zone(zone, highs, lows, closes, c.swing_lookback)
                if dead or first_touch != last:
                    continue  # не первое касание на последнем баре — тишина
                if not _volume_ok_daily(candles):
                    return None
                buf = c.stop_buffer_pct
                if zone.side == "bull":
                    stop = zone.bottom * (1.0 - buf)
                    swing = max(highs[zone.formed_idx + 1 : last]) if last > zone.formed_idx + 1 else 0.0
                    if swing <= price:
                        continue
                    sig = _signal(symbol, c.name, models.TradeDirection.LONG, price, stop, swing, market_regime)
                else:
                    stop = zone.top * (1.0 + buf)
                    swing = min(lows[zone.formed_idx + 1 : last]) if last > zone.formed_idx + 1 else 0.0
                    if swing and swing >= price:
                        continue
                    if not swing:
                        continue
                    sig = _signal(symbol, c.name, models.TradeDirection.SHORT, price, stop, swing, market_regime)
                if sig is not None:
                    return sig
            return None
        except Exception as exc:  # защита: стратегия не должна ронять цикл
            logger.warning("ob_swing %s failed: %s", symbol, exc)
            return None

    # Контрактные заглушки: фактические стоп/тейк несёт Signal из
    # evaluate (зоны/структурные цели); здесь — то же структурное
    # правило по последней живой зоне, запасной вариант как у соседей.
    def calculate_stop_loss(self, entry_price: Decimal, candles: list[models.Candle], atr: float | None = None) -> Decimal:
        try:
            highs = [float(x.high) for x in candles]
            lows = [float(x.low) for x in candles]
            for z in build_zones(highs, lows, self.config.swing_lookback, self.config.max_zones_per_side):
                if z.side == "bull":
                    return Decimal(str(z.bottom * (1.0 - self.config.stop_buffer_pct)))
        except Exception:
            pass
        return entry_price * Decimal("0.995")

    def calculate_take_profit(self, entry_price: Decimal, stop_loss: Decimal, candles: list[models.Candle]) -> list[dict]:
        return [{"price": entry_price * Decimal("1.01"), "fraction": 1.0}]


# ---------------------------------------------------------------------------
# 7.2 breaker_block — ретест сломанного блока с обратной стороны
# ---------------------------------------------------------------------------


@dataclass
class BreakerBlockConfig(StrategyConfig):
    name: str = "breaker_block"
    enabled: bool = True
    swing_lookback: int = OB_SWING_LOOKBACK
    max_zones_per_side: int = OB_MAX_ZONES_PER_SIDE
    stop_buffer_pct: float = OB_STOP_BUFFER_PCT


class BreakerBlockStrategy(BaseStrategy[BreakerBlockConfig]):
    """Сломанный блок живёт с обратной полярностью (механика Люкса).

    Пробитый бычий блок (закрытие ниже зоны) — медвежий брейкер: шорт
    на ПЕРВОМ касании снизу; пробитый медвежий — бычий: лонг на первом
    касании сверху. Возврат цены за зону гасит брейкер. ОДНОРАЗОВОСТЬ
    та же, что у ``ob_swing``.
    """

    preferred_timeframe = "1d"

    def __init__(self, config: BreakerBlockConfig | None = None) -> None:
        super().__init__(config or BreakerBlockConfig())
        self.config: BreakerBlockConfig

    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook=None,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Signal | None:
        c = self.config
        if not c.enabled:
            return None
        try:
            need = 2 * c.swing_lookback + 5
            if len(candles) < need:
                return None
            highs = [float(x.high) for x in candles]
            lows = [float(x.low) for x in candles]
            closes = [float(x.close) for x in candles]
            last = len(candles) - 1
            price = float(current_price or closes[-1])
            for zone in build_zones(highs, lows, c.swing_lookback, c.max_zones_per_side):
                _touch, broken_at, breaker_touch, dead = walk_zone(zone, highs, lows, closes, c.swing_lookback)
                if dead or broken_at is None or breaker_touch != last:
                    continue  # нет слома / касание не первое и не на последнем баре
                if not _volume_ok_daily(candles):
                    return None
                buf = c.stop_buffer_pct
                if zone.side == "bull":
                    # Бычий блок сломан вниз → медвежий брейкер: шорт на
                    # ретесте снизу; стоп над зоной; цель — структурный
                    # минимум после слома.
                    stop = zone.top * (1.0 + buf)
                    swing = min(lows[broken_at + 1 : last]) if last > broken_at + 1 else 0.0
                    if not swing or swing >= price:
                        continue
                    sig = _signal(symbol, c.name, models.TradeDirection.SHORT, price, stop, swing, market_regime)
                else:
                    stop = zone.bottom * (1.0 - buf)
                    swing = max(highs[broken_at + 1 : last]) if last > broken_at + 1 else 0.0
                    if swing <= price:
                        continue
                    sig = _signal(symbol, c.name, models.TradeDirection.LONG, price, stop, swing, market_regime)
                if sig is not None:
                    return sig
            return None
        except Exception as exc:
            logger.warning("breaker_block %s failed: %s", symbol, exc)
            return None

    # Контрактные заглушки (см. ObSwingStrategy): стоп за дальней
    # границей сломанной зоны, запасной вариант как у соседей.
    def calculate_stop_loss(self, entry_price: Decimal, candles: list[models.Candle], atr: float | None = None) -> Decimal:
        try:
            highs = [float(x.high) for x in candles]
            lows = [float(x.low) for x in candles]
            closes = [float(x.close) for x in candles]
            for z in build_zones(highs, lows, self.config.swing_lookback, self.config.max_zones_per_side):
                _t, broken, _bt, dead = walk_zone(z, highs, lows, closes, self.config.swing_lookback)
                if broken is not None and not dead:
                    if z.side == "bull":
                        return Decimal(str(z.top * (1.0 + self.config.stop_buffer_pct)))
                    return Decimal(str(z.bottom * (1.0 - self.config.stop_buffer_pct)))
        except Exception:
            pass
        return entry_price * Decimal("1.005")

    def calculate_take_profit(self, entry_price: Decimal, stop_loss: Decimal, candles: list[models.Candle]) -> list[dict]:
        return [{"price": entry_price * Decimal("0.99"), "fraction": 1.0}]


# ---------------------------------------------------------------------------
# 7.3 maicross — кросс дневных EMA 20/50 (только сам бар кросса)
# ---------------------------------------------------------------------------


@dataclass
class MaiCrossConfig(StrategyConfig):
    name: str = "maicross"
    enabled: bool = True
    fast: int = MAICROSS_FAST
    slow: int = MAICROSS_SLOW
    stop_lookback: int = MAICROSS_STOP_LOOKBACK
    target_lookback: int = MAICROSS_TARGET_LOOKBACK
    stop_buffer_pct: float = OB_STOP_BUFFER_PCT


def _ema_series(values: list[float], period: int) -> list[float] | None:
    """EMA-ряд (посев от values[0] — как в core.utils/indicators)."""
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1.0 - k))
    return out


class MaiCrossStrategy(BaseStrategy[MaiCrossConfig]):
    """Кросс дневных EMA 20/50.

    ОДНОРАЗОВОСТЬ (решение владельца): сигнал — только если знак
    разницы ``EMA20 - EMA50`` сменился ИМЕННО на последнем закрытом
    баре; «состояние пересечения» на второй и последующие дни — тишина.
    """

    preferred_timeframe = "1d"

    def __init__(self, config: MaiCrossConfig | None = None) -> None:
        super().__init__(config or MaiCrossConfig())
        self.config: MaiCrossConfig

    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook=None,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Signal | None:
        c = self.config
        if not c.enabled:
            return None
        try:
            if len(candles) < max(c.slow + 2, c.target_lookback + 2):
                return None
            closes = [float(x.close) for x in candles]
            highs = [float(x.high) for x in candles]
            lows = [float(x.low) for x in candles]
            fast = _ema_series(closes, c.fast)
            slow = _ema_series(closes, c.slow)
            if fast is None or slow is None:
                return None
            prev_diff = fast[-2] - slow[-2]
            last_diff = fast[-1] - slow[-1]
            if prev_diff <= 0.0 < last_diff:
                direction = models.TradeDirection.LONG
            elif prev_diff >= 0.0 > last_diff:
                direction = models.TradeDirection.SHORT
            else:
                return None  # кросс не на последнем закрытом баре — тишина
            if not _volume_ok_daily(candles):
                return None
            price = float(current_price or closes[-1])
            buf = c.stop_buffer_pct
            if direction is models.TradeDirection.LONG:
                stop = min(lows[-c.stop_lookback:]) * (1.0 - buf)
                take = max(highs[-c.target_lookback:])  # структурное сопротивление
                if take <= price:
                    return None
            else:
                stop = max(highs[-c.stop_lookback:]) * (1.0 + buf)
                take = min(lows[-c.target_lookback:])  # структурная поддержка
                if take >= price:
                    return None
            return _signal(symbol, c.name, direction, price, stop, take, market_regime)
        except Exception as exc:
            logger.warning("maicross %s failed: %s", symbol, exc)
            return None

    # Контрактные заглушки: стоп за кач последних дневных баров
    # (то же правило, что в evaluate), тейк — структурный экстремум.
    def calculate_stop_loss(self, entry_price: Decimal, candles: list[models.Candle], atr: float | None = None) -> Decimal:
        try:
            lows = [float(x.low) for x in candles]
            if len(lows) >= self.config.stop_lookback:
                return Decimal(str(min(lows[-self.config.stop_lookback:]) * (1.0 - self.config.stop_buffer_pct)))
        except Exception:
            pass
        return entry_price * Decimal("0.995")

    def calculate_take_profit(self, entry_price: Decimal, stop_loss: Decimal, candles: list[models.Candle]) -> list[dict]:
        try:
            highs = [float(x.high) for x in candles]
            if len(highs) >= self.config.target_lookback:
                return [{"price": Decimal(str(max(highs[-self.config.target_lookback:]))), "fraction": 1.0}]
        except Exception:
            pass
        return [{"price": entry_price * Decimal("1.01"), "fraction": 1.0}]
