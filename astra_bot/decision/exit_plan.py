"""Unified ExitPlan — единая точка правил выхода (бэклог B8, этап 2).

Решения владельца от 11.09.2026 (реализованы здесь):
  1. ОДИН осмысленный тейк в зоне 2-2.3R вместо лестницы частичных
     фиксаций (tp1=1R срабатывал в 2/123 сделок — причины и цифры в
     docs/EXIT_PLAN_MAP.md §7).
  2. ЖЁСТКАЯ иерархия: ликвидация (live) > жёсткий стоп > план.
     План НИКОГДА не двигает стоп в сторону увеличения риска и не
     отключает стоп (инвариант «только подтягивание» в apply_tighter).
     В paper ликвидационная проверка остаётся ПОСЛЕ стопов (по mark
     price) — порядок в process_symbol не менялся.
  3. Один исполнитель правил для обоих контуров: paper — живые семантики
     (живой бар/цена), бэктестер — закрытые бары. Чистые функции правил
     ниже не зависят от брокера — их используют оба контура.

Состав плана (SMART_DEFAULT — значения перенесены из прежнего
ExitController.SMART_DEFAULT_PLAN + safety ExitManager, чтобы поведение
менялось только там, где велел владелец):
  STOP-правила (до проверки уровней, TZ §16):
    D1_STRUCTURAL — структурный стоп пересчитывается на каждом НОВОМ
                    ЗАКРЫТОМ баре ТФ позиции, ТОЛЬКО в пользу позиции;
    BREAKEVEN     — нетто-безубыток после trigger_r × R;
    TRAILING      — трейлинг k×ATR (только подтягивание).
  FORCED-правила (закрытие по цене; порядок атрибуции фиксирован):
    MAE_CUT → TIME_STOP(гипотеза) → MOMENTUM_EXIT(гипотеза) →
    REGIME_EXIT → MAX_HOLD → VOL_EXPANSION (два последних — safety,
    активны всегда, параметры из ExitManagerConfig).
  TAKE: один уровень (clamp RR в [take_rr_min, take_rr_max]).

Гипотезы выхода (TZ §16/§17): активная гипотеза задаёт вариант стопа
(ATR_STOP/STRUCTURE_STOP/TRAILING/BREAKEVEN) и/или forced-правило
(TIME_STOP/MOMENTUM_EXIT/REGIME_EXIT); safety и один тейк действуют
и для гипотезных планов. Инвариант «только подтягивание» применён
ко ВСЕМ стоп-правилам, включая гипотезные (иерархия п.2).
"""

from __future__ import annotations

import logging
import statistics
import time as _time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .exit_controller import FORCED_CLOSE_VARIANTS, _atr, _ema
from .exit_manager import ExitManagerConfig, tr_series

logger = logging.getLogger(__name__)

SMART_DEFAULT = "SMART_DEFAULT"

# Режимы, против которых позиция не выживает (перенесено из
# ExitController._ADVERSE_REGIMES — единственный источник правила).
ADVERSE_REGIMES = {
    "long": {"PANIC", "HIGH_VOLATILITY", "STRONG_BEAR_TREND", "WEAK_BEAR_TREND"},
    "short": {"PANIC", "HIGH_VOLATILITY", "STRONG_BULL_TREND", "WEAK_BULL_TREND"},
}

# С плечом цена против нас ест маржу быстрее — триггеры срезаются на 25%
# (перенесено из ExitController._LEVERAGE_TRIGGER_SCALE).
LEVERAGE_TRIGGER_SCALE = 0.75


@dataclass
class ExitPlanParams:
    """Параметры плана выхода (SMART_DEFAULT; гипотеза переопределяет часть)."""

    breakeven_trigger_r: float = 0.8
    breakeven_net: bool = True
    trailing_k: float = 2.0
    mae_cut_r: float = 0.9
    regime_exit_regimes: tuple[str, ...] = ("PANIC",)
    time_stop_bars: int | None = None  # только гипотеза TIME_STOP
    momentum_ema: int | None = None  # только гипотеза MOMENTUM_EXIT
    # D1: структурный стоп по закрытым барам (параметры — как у
    # structural_stop на входе, exit_controller.py).
    structural_lookback: int = 15
    structural_buffer_atr: float = 0.15
    structural_buffer_pct: float = 0.003
    # Решение владельца №1: ОДИН тейк в зоне 2-2.3R.
    take_rr_min: float = 2.0
    take_rr_max: float = 2.3
    atr_stop_k: float = 2.0  # гипотеза ATR_STOP
    structure_lookback: int = 10  # гипотеза STRUCTURE_STOP


def smart_params() -> ExitPlanParams:
    """Параметры смарт-дефолта (совместимы с прежним поведением + D1/тейк)."""
    return ExitPlanParams()


def params_for_variant(variant: str, params: dict[str, Any] | None) -> ExitPlanParams:
    """ExitPlanParams из параметров активной гипотезы (или смарт-дефолт)."""
    p = ExitPlanParams()
    if not params:
        return p
    if variant == "BREAKEVEN":
        p.breakeven_trigger_r = float(params.get("trigger_r", 1.0))
        p.breakeven_net = bool(params.get("net", False))
    elif variant == "TRAILING":
        p.trailing_k = float(params.get("k", 2.0))
    elif variant == "ATR_STOP":
        p.atr_stop_k = float(params.get("k", 2.0))
    elif variant == "STRUCTURE_STOP":
        p.structure_lookback = int(params.get("lookback", 10))
    elif variant == "TIME_STOP":
        p.time_stop_bars = int(params.get("bars", 12))
    elif variant == "MOMENTUM_EXIT":
        p.momentum_ema = int(params.get("ema", 9))
    elif variant == "REGIME_EXIT":
        p.regime_exit_regimes = tuple(params.get("exit_regimes", ["PANIC"]))
    return p


def clamp_take_rr(
    entry: Decimal,
    stop: Decimal,
    take: Decimal,
    direction: str,
    rr_min: float = 2.0,
    rr_max: float = 2.3,
) -> Decimal:
    """ОДИН тейк в зоне [rr_min, rr_max] × R (решение владельца №1).

    RR тейка стратегии зажимается в зону: меньше take_rr_min — тейк
    уводится на rr_min×R (осмысленный тейк вместо близких фиксаций),
    больше take_rr_max — срезается до rr_max×R (реалистичная цель).
    take <= 0 (флип-стратегии без тейка) возвращается как есть.
    """
    take = Decimal(str(take))
    entry = Decimal(str(entry))
    if take <= 0:
        return take
    risk = abs(entry - Decimal(str(stop)))
    if risk <= 0:
        return take
    rr = abs(take - entry) / risk
    if rr < Decimal(str(rr_min)):
        rr = Decimal(str(rr_min))
    elif rr > Decimal(str(rr_max)):
        rr = Decimal(str(rr_max))
    if direction == "long":
        return entry + risk * rr
    return entry - risk * rr


def structural_tighten(
    direction: str,
    entry: Decimal,
    current_stop: Decimal,
    closed_bars: list[Any],
    lookback: int = 15,
    buffer_atr: float = 0.15,
    buffer_pct: float = 0.003,
) -> Decimal | None:
    """D1: структурный стоп по ЗАКРЫТЫМ барам — только в пользу позиции.

    Свинг по теням окна последних закрытых баров с буфером
    max(buffer_pct × |вход|, buffer_atr × ATR). Для long кандидат
    (min low − буфер) принимается ТОЛЬКО если он ВЫШЕ текущего стопа
    (раньше триггер, меньше риск); для short — зеркально. Расширять
    стоп правило не умеет по построению (иерархия п.2).
    """
    try:
        if len(closed_bars) < 3:
            return None
        window = list(closed_bars)[-int(lookback):]
        atr = _atr(list(closed_bars), len(closed_bars) - 1)
        if atr <= 0:
            return None
        entry_f = float(entry)
        cur = float(current_stop)
        buf = max(buffer_pct * entry_f, buffer_atr * atr)
        if direction == "long":
            swing = min(float(b.low) for b in window)
            candidate = swing - buf
            if candidate > cur:
                return Decimal(str(candidate))
        else:
            swing = max(float(b.high) for b in window)
            candidate = swing + buf
            if candidate < cur:
                return Decimal(str(candidate))
    except (TypeError, ValueError, IndexError):
        return None
    return None


def breakeven_target(
    direction: str,
    current_stop: Decimal,
    entry: Decimal,
    mfe_r: float,
    trigger_r: float,
    net_target: Decimal | None,
) -> Decimal | None:
    """Стоп в безубыток после trigger_r × R. net_target=None → сырой вход
    (гипотезный BREAKEVEN, прежнее поведение); Decimal → нетто-цель
    (смарт-дефолт, broker.net_breakeven_price). Только подтягивание."""
    if mfe_r < trigger_r:
        return None
    target = net_target if net_target is not None else Decimal(str(entry))
    if direction == "long" and current_stop < target:
        return target
    if direction == "short" and current_stop > target:
        return target
    return None


def trailing_target(
    direction: str,
    current_stop: Decimal,
    anchor_high: Decimal | None,
    anchor_low: Decimal | None,
    atr: float,
    k: float,
) -> Decimal | None:
    """Трейлинг k×ATR от экстремума позиции. Только подтягивание."""
    if atr <= 0:
        return None
    if direction == "long":
        if anchor_high is None:
            return None
        candidate = Decimal(str(float(anchor_high) - k * atr))
        if candidate > current_stop:
            return candidate
    else:
        if anchor_low is None:
            return None
        candidate = Decimal(str(float(anchor_low) + k * atr))
        if candidate < current_stop:
            return candidate
    return None


def apply_tighter(pos: Any, candidate: Decimal | None) -> bool:
    """Применить кандидата стопа, если он уменьшает риск.

    Инвариант иерархии п.2: стоп двигается только в сторону уменьшения
    риска (long — вверх, short — вниз) и никогда не снимается.
    Работает и с PaperPosition.direction, и с бэктестерным Trade.side.
    """
    if candidate is None:
        return False
    direction = getattr(pos, "direction", None) or getattr(pos, "side", "")
    if direction == "long" and candidate > pos.stop_loss:
        pos.stop_loss = candidate
        return True
    if direction == "short" and candidate < pos.stop_loss:
        pos.stop_loss = candidate
        return True
    return False


def primary_for(candles_by_tf: dict[str, list[Any]], pos: Any) -> list[Any]:
    """Основной список баров позиции: ТФ позиции или 1h-фолбэк."""
    tf = getattr(pos, "timeframe", "") or ""
    return candles_by_tf.get(tf) or candles_by_tf.get("1h") or []


class ExitPlanEngine:
    """Исполнитель плана выхода (paper-контур; правила — чистые функции выше).

    Источники параметров (совместимость с прежними слоями):
      гипотезы и смарт-флаг — ExitController; safety-параметры
      (MAX_HOLD/VOL_EXPANSION) — ExitManagerConfig из ExitManager.
    """

    def __init__(self, exit_controller: Any, exit_manager: Any) -> None:
        self.exit_controller = exit_controller
        self.exit_manager = exit_manager

    # ------------------------------------------------------------- helpers
    def _plan(self, pos: Any, regime: str) -> tuple[str, ExitPlanParams]:
        variant, raw = self.exit_controller.plan_for(
            getattr(pos, "strategy", ""), getattr(pos, "regime", "") or regime
        )
        return variant, params_for_variant(variant, raw)

    @staticmethod
    def _closed_bars(candles: list[Any]) -> list[Any]:
        """Закрытые бары ТФ (последний бар фида — формирующийся, А5)."""
        if len(candles) > 1:
            return list(candles[:-1])
        return list(candles)

    # ------------------------------------------------------- stop rules
    def adjust_stops(
        self,
        broker: Any,
        symbol: str,
        positions: list[Any],
        candles_by_tf: dict[str, list[Any]],
        primary: list[Any],
        last_bar: Any,
    ) -> None:
        """STOP-правила плана. Вызывается ДО broker.check_exits (TZ §16).

        D1: структурный стоп — на каждом новом закрытом баре ТФ позиции.
        BE/трейлинг/гипотезные варианты — на каждом вызове (как прежний
        ExitController). Только подтягивание, стоп не снимается.
        """
        if not positions:
            return
        smart = bool(getattr(self.exit_controller, "smart_default", True))
        any_moved = False
        for pos in positions:
            variant, p = self._plan(pos, "")
            if variant == "STATIC_TP" and not smart:
                continue
            tf = getattr(pos, "timeframe", "") or ""
            tf_candles = candles_by_tf.get(tf) or candles_by_tf.get("1h") or primary
            closed = self._closed_bars(tf_candles)

            # Новый закрытый бар ТФ позиции?
            new_ts = int(getattr(closed[-1], "open_time", 0)) if closed else 0
            is_new_bar = bool(new_ts) and int(
                getattr(pos, "plan_last_bar_ts", 0) or 0
            ) != new_ts

            entry = pos.entry_price
            risk = pos.risk_distance or abs(entry - pos.stop_loss)
            high = pos.highest_price if pos.highest_price is not None else last_bar.high
            low = pos.lowest_price if pos.lowest_price is not None else last_bar.low

            # 1) D1: структурный стоп по закрытым барам (только новый бар).
            if is_new_bar and risk > 0:
                if apply_tighter(
                    pos,
                    structural_tighten(
                        pos.direction, entry, pos.stop_loss, closed,
                        p.structural_lookback, p.structural_buffer_atr,
                        p.structural_buffer_pct,
                    ),
                ):
                    any_moved = True
                    logger.info(
                        "PLAN %s %s: D1-structural stop -> %s",
                        symbol, pos.direction, pos.stop_loss,
                    )

            # 2) Безубыток + трейлинг.
            lev = float(getattr(pos, "leverage", 1) or 1)
            scale = LEVERAGE_TRIGGER_SCALE if lev > 1 else 1.0
            if variant == "STATIC_TP":
                # Смарт-дефолт: нетто-БУ (0.8R×scale) + трейлинг 2×ATR.
                if risk > 0:
                    mfe_r = (
                        (float(high) - float(entry)) / float(risk)
                        if pos.direction == "long"
                        else (float(entry) - float(low)) / float(risk)
                    )
                    net = None
                    if p.breakeven_net and hasattr(broker, "net_breakeven_price"):
                        net = broker.net_breakeven_price(pos)
                    if apply_tighter(
                        pos,
                        breakeven_target(
                            pos.direction, pos.stop_loss, entry,
                            mfe_r, p.breakeven_trigger_r * scale, net,
                        ),
                    ):
                        any_moved = True
                if len(primary) >= 2:
                    if apply_tighter(
                        pos,
                        trailing_target(
                            pos.direction, pos.stop_loss, pos.highest_price,
                            pos.lowest_price, _atr(list(primary), len(primary) - 2),
                            p.trailing_k,
                        ),
                    ):
                        any_moved = True
            else:
                # Гипотезный вариант стопа (прежние правила _adjust_stop,
                # с инвариантом «только подтягивание»).
                if variant == "ATR_STOP" and pos.bars_held == 1 and len(primary) >= 2:
                    atr = _atr(list(primary), len(primary) - 2)
                    if atr > 0:
                        cand = (
                            entry - Decimal(str(p.atr_stop_k * atr))
                            if pos.direction == "long"
                            else entry + Decimal(str(p.atr_stop_k * atr))
                        )
                        if apply_tighter(pos, cand):
                            any_moved = True
                elif variant == "TRAILING" and len(primary) >= 2:
                    if apply_tighter(
                        pos,
                        trailing_target(
                            pos.direction, pos.stop_loss, pos.highest_price,
                            pos.lowest_price, _atr(list(primary), len(primary) - 2),
                            p.trailing_k,
                        ),
                    ):
                        any_moved = True
                elif variant == "STRUCTURE_STOP":
                    i = len(primary) - 1
                    window = list(primary)[max(0, i - p.structure_lookback):i]
                    if window:
                        level = (
                            min(float(b.low) for b in window)
                            if pos.direction == "long"
                            else max(float(b.high) for b in window)
                        )
                        if apply_tighter(pos, Decimal(str(level))):
                            any_moved = True
                elif variant == "BREAKEVEN" and risk > 0:
                    mfe_r = (
                        (float(high) - float(entry)) / float(risk)
                        if pos.direction == "long"
                        else (float(entry) - float(low)) / float(risk)
                    )
                    if apply_tighter(
                        pos,
                        breakeven_target(
                            pos.direction, pos.stop_loss, entry,
                            mfe_r, p.breakeven_trigger_r, None,
                        ),
                    ):
                        any_moved = True

            if is_new_bar and new_ts:
                pos.plan_last_bar_ts = new_ts

            if any_moved:
                logger.info(
                    "PLAN %s %s: стоп подтянут планом %s -> %s",
                    symbol, pos.direction, variant, pos.stop_loss,
                )
        if any_moved:
            broker.save()

    # ----------------------------------------------------- forced rules
    def forced_closes(
        self,
        broker: Any,
        symbol: str,
        positions: list[Any],
        candles_by_tf: dict[str, list[Any]],
        bar_close: Decimal,
        current_price: Decimal,
        regime: str,
        now_ms: int | None = None,
    ) -> list[Any]:
        """FORCED-правила плана — закрытия по цене.

        Порядок атрибуции: MAE_CUT → TIME_STOP → MOMENTUM_EXIT →
        REGIME_EXIT → MAX_HOLD → VOL_EXPANSION. Жёсткий стоп и тейк
        СТАРШЕ (проверены раньше в process_symbol — иерархия п.2).
        Возвращает ClosedTrade закрываемых позиций.
        """
        closed: list[Any] = []
        if not positions:
            return closed
        smart = bool(getattr(self.exit_controller, "smart_default", True))
        safety: ExitManagerConfig = self.exit_manager.config
        now = int(now_ms if now_ms is not None else _time.time() * 1000)
        for pos in positions:
            variant, p = self._plan(pos, regime)
            price_bar = Decimal(str(bar_close))
            price_live = Decimal(str(current_price))
            reason: str | None = None

            # --- правила плана ---
            if variant == "STATIC_TP" and smart:
                reason = self._smart_forced(pos, p, price_bar, regime)
            elif variant in FORCED_CLOSE_VARIANTS:
                reason = self._hypothesis_forced(
                    pos, variant, p, price_bar, primary_for(candles_by_tf, pos),
                    regime,
                )

            # --- safety (всегда; параметры ExitManager) ---
            if reason is None:
                if pos.opened_at and (
                    now - int(pos.opened_at)
                ) >= safety.max_hold_seconds * 1000:
                    reason = "MAX_HOLD"
            if reason is None:
                cs = candles_by_tf.get(pos.timeframe or "") or candles_by_tf.get("1h")
                if cs and len(cs) >= safety.vol_lookback:
                    series = tr_series(cs, safety.vol_window)
                    if len(series) >= 5:
                        med = statistics.median(series[:-1])
                        if med > 0 and series[-1] >= safety.vol_expansion_ratio * med:
                            reason = "VOL_EXPANSION"

            if reason is not None:
                trade = broker.close_position(pos.id, price_live, reason)
                if trade is not None:
                    logger.info(
                        "PLAN %s %s: forced %s @ %s (план %s)",
                        symbol, pos.direction, reason, price_live, variant,
                    )
                    closed.append(trade)
        if closed:
            broker.save()
        return closed

    def _smart_forced(
        self, pos: Any, p: ExitPlanParams, close: Decimal, regime: str
    ) -> str | None:
        """MAE_CUT/REGIME_EXIT смарт-дефолта (по close; прежние пороги)."""
        lev = float(getattr(pos, "leverage", 1) or 1)
        scale = LEVERAGE_TRIGGER_SCALE if lev > 1 else 1.0
        risk = pos.risk_distance or abs(pos.entry_price - pos.stop_loss)
        if risk > 0:
            r_now = (
                (float(close) - float(pos.entry_price)) / float(risk)
                if pos.direction == "long"
                else (float(pos.entry_price) - float(close)) / float(risk)
            )
            if r_now <= -p.mae_cut_r * scale:
                return "mae_cut"
        adverse = ADVERSE_REGIMES.get(pos.direction, set())
        if regime in adverse and regime != (pos.regime or ""):
            return "regime_exit"
        return None

    def _hypothesis_forced(
        self, pos: Any, variant: str, p: ExitPlanParams,
        close: Decimal, primary: list[Any], regime: str,
    ) -> str | None:
        """Forced-правила активной гипотезы (прежние _forced_close_hit)."""
        if variant == "TIME_STOP" and p.time_stop_bars is not None:
            return "time_stop" if pos.bars_held >= p.time_stop_bars else None
        if variant == "MOMENTUM_EXIT" and p.momentum_ema is not None:
            span = p.momentum_ema
            if len(primary) < span:
                return None
            ema = _ema([float(b.close) for b in primary], span)
            if pos.direction == "long":
                return "momentum_exit" if float(close) < ema[-1] else None
            return "momentum_exit" if float(close) > ema[-1] else None
        if variant == "REGIME_EXIT":
            if regime in p.regime_exit_regimes and regime != (pos.regime or ""):
                return "regime_exit"
            return None
        return None
