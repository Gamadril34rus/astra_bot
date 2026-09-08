"""Exit Controller: применение выбранных стратегий выхода (TZ §16/§17).

Поведение по умолчанию = текущее (STATIC_TP + исходный стоп + трейлинг
брокера после TP1): live-контур НЕ меняется, пока Hypothesis Engine не
допустил вариант выхода до ACTIVE (train+validation+OOS+walk-forward+
stress). Контроллер вызывается на каждом новом баре:

    broker.update_extremes(bar)
    exit_controller.apply(broker, symbol, bar, bars, regime)
    broker.check_exits(bar)

Варианты (параметры — из гипотезы):
    STATIC_TP      — без действий
    ATR_STOP       — на первом баре позиция стоп = k*ATR14
    STRUCTURE_STOP — стоп на swing low/high последних lookback баров
    TRAILING       — стоп = экстремум ± k*ATR
    BREAKEVEN      — стоп в точку входа после trigger_r × R (по экстремуму)
    TIME_STOP      — закрытие через n баров (в close)
    MOMENTUM_EXIT  — закрытие в close при пересечении цены EMA9
    REGIME_EXIT    — закрытие в close при смене режима в exit_regimes

Умный набор по умолчанию (SMART_DEFAULT_PLAN): пока гипотез нет,
работает не голый STATIC_TP, а связка BREAKEVEN(нетто) + TRAILING +
MAE_CUT (ранний срез при движении против позиции) + REGIME_EXIT
(смена режима против направления). При плече >1 триггеры срезаются
на 25% раньше — с плечом цена против нас опаснее.
"""

from __future__ import annotations

import logging
from typing import Any

from .broker import ClosedTrade, PaperBroker

logger = logging.getLogger(__name__)

DEFAULT_PLAN = ("STATIC_TP", {})

# Умный набор по умолчанию (paper/sim): работает, пока Hypothesis Engine
# не продвинул собственный план выхода. Состав:
#   BREAKEVEN  — стоп в НЕТТО-безубыток (вход+комиссии+плата за плечо) по MFE
#   TRAILING   — трейлинг k*ATR (только подтягивание, не ослабление)
#   MAE_CUT    — цена ушла против на mae_r*R без отката → режем в close
#                («отторговка»: средний убыток меньше полного стопа)
#   REGIME_EXIT— режим развернулся ПРОТИВ направления позиции → выход в close
SMART_DEFAULT_PLAN: list[tuple[str, dict[str, Any]]] = [
    ("BREAKEVEN", {"trigger_r": 0.8, "net": True}),
    ("TRAILING", {"k": 2.0}),
    ("MAE_CUT", {"mae_r": 0.9}),
    ("REGIME_EXIT", {"exit_regimes": ["PANIC"]}),
]

# Режимы, против которых позиция не выживает: лонг умирает в медвежьих,
# шорт — в бычьих. PANIC/HIGH_VOLATILITY — против любого направления.
_ADVERSE_REGIMES = {
    "long": {"PANIC", "HIGH_VOLATILITY", "STRONG_BEAR_TREND", "WEAK_BEAR_TREND"},
    "short": {"PANIC", "HIGH_VOLATILITY", "STRONG_BULL_TREND", "WEAK_BULL_TREND"},
}

# С плечом цена против нас ест маржу быстрее — триггеры срезаются на 25%.
_LEVERAGE_TRIGGER_SCALE = 0.75

FORCED_CLOSE_VARIANTS = ("TIME_STOP", "MOMENTUM_EXIT", "REGIME_EXIT")


def _ema(values: list[float], span: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (span + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def _atr(bars: Any, index: int, period: int = 14) -> float:
    start = max(1, index - period + 1)
    trs = []
    for idx in range(start, index + 1):
        h, lo = float(bars[idx].high), float(bars[idx].low)
        pc = float(bars[idx - 1].close)
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return sum(trs) / len(trs) if trs else 0.0


class ExitController:
    def __init__(self, hypotheses: Any, smart_default: bool = True) -> None:
        self.hypotheses = hypotheses
        self.smart_default = smart_default

    # ------------------------------------------------------------- plans
    def plan_for(self, strategy: str, regime: str) -> tuple[str, dict[str, Any]]:
        """Активный план выхода для (стратегия, режим), иначе STATIC_TP."""
        if not strategy:
            return DEFAULT_PLAN
        best = None
        for hyp in self.hypotheses.for_strategy(strategy):
            if hyp.status.value != "ACTIVE":
                continue
            variant = hyp.features.get("exit_variant")
            if not variant:
                continue
            hyp_regime = hyp.conditions.get("regime", "ANY")
            if hyp_regime not in ("ANY", regime):
                continue
            if best is None or hyp.confidence > best.confidence:
                best = hyp
        if best is None:
            return DEFAULT_PLAN
        return str(best.features["exit_variant"]), dict(best.features.get("exit_params") or {})

    # ------------------------------------------------------------- apply
    def apply(
        self,
        broker: PaperBroker,
        symbol: str,
        bar: Any,
        bars: list[Any],
        current_regime: str,
    ) -> list[ClosedTrade]:
        """Скорректировать стопы / вынудить закрытия по плану. Возвращает
        сделки, закрытые вынужденно (TIME/MOMENTUM/REGIME)."""
        closed: list[ClosedTrade] = []
        close = float(bar.close)
        for pos in list(broker.positions):
            if pos.symbol != symbol:
                continue
            variant, params = self.plan_for(pos.strategy, pos.regime or current_regime)
            if variant == "STATIC_TP":
                if self.smart_default:
                    smart = self._apply_smart(broker, pos, bar, bars, current_regime)
                    if smart is not None:
                        closed.append(smart)
                continue
            reason = variant.lower()
            if variant in FORCED_CLOSE_VARIANTS:
                if self._forced_close_hit(variant, params, pos, bar, bars, current_regime):
                    trade = broker.close_position(pos.id, _dec(close), reason)
                    if trade:
                        closed.append(trade)
                        logger.info(
                            "EXIT %s %s: %s (гипотеза exit, plan=%s)",
                            symbol, pos.direction, reason, variant,
                        )
                continue
            self._adjust_stop(broker, pos, variant, params, bar, bars)
        if closed:
            broker.save()
        return closed

    # ----------------------------------------------------- smart default
    def _apply_smart(
        self,
        broker: PaperBroker,
        pos: Any,
        bar: Any,
        bars: list[Any],
        current_regime: str,
    ) -> ClosedTrade | None:
        """Умный набор выходов для одной позиции (без активной гипотезы).

        Порядок: вынужденные закрытия (MAE_CUT / REGIME_EXIT) в close,
        затем подтягивание стопа (нетто-BREAKEVEN + TRAILING). С плечом>1
        пороги срезаются на 25% (потери по марже быстрее).
        """
        lev = float(getattr(pos, "leverage", 1) or 1)
        scale = _LEVERAGE_TRIGGER_SCALE if lev > 1 else 1.0
        entry = float(pos.entry_price)
        close = float(bar.close)
        risk = float(pos.risk_distance) if pos.risk_distance else abs(
            entry - float(pos.stop_loss)
        )

        # 1) Ранний срез: цена против нас глубже порога — выходим в close,
        # не дожидаясь полного стопа (средний убыток меньше).
        mae_r = 0.9 * scale
        if risk > 0:
            r_now = (close - entry) / risk if pos.direction == "long" \
                else (entry - close) / risk
            if r_now <= -mae_r:
                trade = broker.close_position(pos.id, _dec(close), "mae_cut")
                if trade:
                    logger.info(
                        "SMART %s %s: MAE_CUT при %.2fR (порог %.2fR, плечо %.1fx)",
                        pos.symbol, pos.direction, r_now, -mae_r, lev,
                    )
                return trade

        # 2) Режим развернулся против направления — отторговка в close.
        adverse = _ADVERSE_REGIMES.get(pos.direction, set())
        if current_regime in adverse and current_regime != (pos.regime or ""):
            trade = broker.close_position(pos.id, _dec(close), "regime_exit")
            if trade:
                logger.info(
                    "SMART %s %s: REGIME_EXIT (%s против %s)",
                    pos.symbol, pos.direction, current_regime, pos.regime,
                )
            return trade

        # 3) Стоп: нетто-безубыток по достигнутому MFE + трейлинг k*ATR.
        high = float(pos.highest_price if pos.highest_price is not None else bar.high)
        low = float(pos.lowest_price if pos.lowest_price is not None else bar.low)
        trigger_r = 0.8 * scale
        if risk > 0:
            mfe_r = (high - entry) / risk if pos.direction == "long" \
                else (entry - low) / risk
            if mfe_r >= trigger_r:
                target = (
                    broker.net_breakeven_price(pos)
                    if hasattr(broker, "net_breakeven_price")
                    else _dec(entry)
                )
                improve = (
                    pos.direction == "long" and float(pos.stop_loss) < float(target)
                ) or (
                    pos.direction == "short" and float(pos.stop_loss) > float(target)
                )
                if improve:
                    pos.stop_loss = target
        self._smart_trail(pos, bars, k=2.0)
        return None

    @staticmethod
    def _smart_trail(pos: Any, bars: list[Any], k: float = 2.0) -> None:
        """Трейлинг k*ATR: стоп только подтягивается, не ослабляется."""
        if len(bars) < 2:
            return
        atr = _atr(bars, len(bars) - 2)
        if atr <= 0:
            return
        if pos.direction == "long":
            anchor = pos.highest_price if pos.highest_price is not None else bars[-1].high
            new_stop = float(anchor) - k * atr
            if new_stop > float(pos.stop_loss):
                pos.stop_loss = _dec(new_stop)
        else:
            anchor = pos.lowest_price if pos.lowest_price is not None else bars[-1].low
            new_stop = float(anchor) + k * atr
            if new_stop < float(pos.stop_loss):
                pos.stop_loss = _dec(new_stop)

    # --------------------------------------------------------- forced
    @staticmethod
    def _forced_close_hit(
        variant: str, params: dict[str, Any], pos: Any, bar: Any,
        bars: list[Any], current_regime: str,
    ) -> bool:
        if variant == "TIME_STOP":
            return pos.bars_held >= int(params.get("bars", 12))
        if variant == "MOMENTUM_EXIT":
            span = int(params.get("ema", 9))
            if len(bars) < span:
                return False
            ema = _ema([float(b.close) for b in bars], span)
            close = float(bar.close)
            if pos.direction == "long":
                return close < ema[-1]
            return close > ema[-1]
        if variant == "REGIME_EXIT":
            exit_regimes = set(params.get("exit_regimes", ["PANIC"]))
            return (
                current_regime in exit_regimes
                and current_regime != (pos.regime or "")
            )
        return False
        return False

    # --------------------------------------------------------- stops
    def _adjust_stop(
        self, broker: PaperBroker, pos: Any, variant: str,
        params: dict[str, Any], bar: Any, bars: list[Any],
    ) -> None:
        if not bars:
            return
        i = len(bars) - 1
        entry = float(pos.entry_price)
        # R-единица — исходный риск позиции (не текущий стоп: он может
        # уже быть подтянут трейлингом/BREAKEVEN).
        risk = float(pos.risk_distance) if pos.risk_distance else abs(
            entry - float(pos.stop_loss)
        )
        high = pos.highest_price if pos.highest_price is not None else float(bar.high)
        low = pos.lowest_price if pos.lowest_price is not None else float(bar.low)

        if variant == "ATR_STOP":
            if pos.bars_held != 1:
                return
            # АТР по данным до текущего бара (без lookahead).
            atr = _atr(bars, i - 1) if i >= 1 else 0.0
            if atr <= 0:
                return
            k = float(params.get("k", 2.0))
            pos.stop_loss = _dec(entry - k * atr) if pos.direction == "long" \
                else _dec(entry + k * atr)
            return

        if variant == "TRAILING":
            atr = _atr(bars, i - 1) if i >= 1 else 0.0
            if atr <= 0:
                return
            k = float(params.get("k", 2.0))
            if pos.direction == "long":
                new_stop = float(high) - k * atr
                if new_stop > float(pos.stop_loss):
                    pos.stop_loss = _dec(new_stop)
            else:
                new_stop = float(low) + k * atr
                if new_stop < float(pos.stop_loss):
                    pos.stop_loss = _dec(new_stop)
            return

        if variant == "STRUCTURE_STOP":
            lookback = int(params.get("lookback", 10))
            window = bars[max(0, i - lookback):i]  # до текущего бара
            if not window:
                return
            level = min(float(b.low) for b in window) if pos.direction == "long" \
                else max(float(b.high) for b in window)
            if (pos.direction == "long" and level > float(pos.stop_loss)) or (pos.direction == "short" and level < float(pos.stop_loss)):
                pos.stop_loss = _dec(level)
            return

        if variant == "BREAKEVEN":
            if risk <= 0:
                return
            mfe_r = (float(high) - entry) / risk if pos.direction == "long" \
                else (entry - float(low)) / risk
            trigger = float(params.get("trigger_r", 1.0))
            if mfe_r >= trigger:
                if (pos.direction == "long" and float(pos.stop_loss) < entry) or (pos.direction == "short" and float(pos.stop_loss) > entry):
                    pos.stop_loss = _dec(entry)
            return


def _dec(value: float):
    from decimal import Decimal

    return Decimal(str(value))

def structural_stop(
    entry: Any,
    stop: Any,
    direction: str,
    bars: list[Any],
    lookback: int = 15,
    buffer_atr: float = 0.15,
    buffer_pct: float = 0.003,
    max_expand_r: float = 2.2,
) -> Any:
    """Структурный стоп: за ближайший свинг-экстремум по ТЕНЯМ.

    Стратегии иногда ставят стоп «в воздухе» (фикс. процент/ATR) внутри
    зоны шума — его выбивает фитилём до реального разворота. Здесь стоп
    выносится за min(low)/max(high) последних lookback закрытых баров с
    буфером max(0.3% цены, 0.15*ATR). Правила:
      - только УХУДШЕНИЕ в безопасную сторону (дальше от входа);
      - не дальше max_expand_r исходного риска (R-бюджет не взрывается —
        позиция ресайзится движком уже по новому стопу);
      - данных мало / R=0 — стоп не трогаем.
    """
    try:
        r0 = abs(float(entry) - float(stop))
        if r0 <= 0 or len(bars) < 3:
            return stop
        window = bars[-lookback:]
        atr = _atr(bars, len(bars) - 1)
        if direction == "long":
            swing = min(float(b.low) for b in window)
        else:
            swing = max(float(b.high) for b in window)
        buf = max(buffer_pct * float(entry), buffer_atr * atr)
        candidate = swing - buf if direction == "long" else swing + buf
        cur = float(stop)
        dist = abs(float(entry) - candidate)
        if direction == "long":
            ok_further = candidate < cur
        else:
            ok_further = candidate > cur
        if ok_further and dist <= max_expand_r * r0:
            return _dec(candidate)
    except (TypeError, ValueError, IndexError):
        pass
    return stop
