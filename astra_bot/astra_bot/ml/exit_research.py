"""Exit Research: исследование и выбор стратегий выхода (TZ §16/§17).

8 вариантов выхода:
    STATIC_TP      — фиксированный тейк-профит (текущее поведение брокера)
    ATR_STOP       — стоп = k × ATR
    STRUCTURE_STOP — стоп на последнем значимом уровне (swing low/high)
    TRAILING       — трейлинг-стоп (k × ATR от максимума цены в пользу)
    BREAKEVEN      — стоп в точку входа после trigger_R × R
    TIME_STOP      — закрытие через n баров независимо от цены
    MOMENTUM_EXIT  — закрытие при развороте импульса (цена за EMA)
    REGIME_EXIT    — закрытие при смене режима в неблагоприятный

Принципы (TZ §17):
- «идеальный» выход, подогнанный под тест, недопустим: вариант
  становится ACTIVE только через полноценный lifecycle гипотезы
  (train + validation + OOS + walk-forward + stress, TZ §11);
- MFE/MAE отслеживаются и учитываются в метриках;
- пока ACTIVE-гипотезы нет — работает дефолт (STATIC_TP + исходный
  стоп), т.е. live-поведение не меняется без доказательств.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class BarLike(Protocol):
    """Свеча: достаточно high/low/close/open_time."""

    high: float
    low: float
    close: float


# ---------------------------------------------------------------------------
# Варианты и их параметры
# ---------------------------------------------------------------------------

EXIT_VARIANTS: dict[str, dict[str, Any]] = {
    "STATIC_TP": {"params": {}, "desc": "фиксированный тейк (текущее поведение)"},
    "ATR_STOP": {"params": {"k": 2.0}, "desc": "стоп = k*ATR14"},
    "STRUCTURE_STOP": {"params": {"lookback": 10}, "desc": "стоп на swing low/high"},
    "TRAILING": {"params": {"k": 2.0}, "desc": "трейлинг k*ATR от экстремума"},
    "BREAKEVEN": {"params": {"trigger_r": 1.0}, "desc": "стоп в точку входа после trigger_r R"},
    "TIME_STOP": {"params": {"bars": 12}, "desc": "закрыть через n баров"},
    "MOMENTUM_EXIT": {"params": {"ema": 9}, "desc": "закрыть при развороте цены относительно EMA"},
    "REGIME_EXIT": {"params": {"exit_regimes": ["PANIC", "HIGH_VOLATILITY", "UNKNOWN"]},
                    "desc": "закрыть при неблагоприятном смене режима"},
}


@dataclass
class EntryEvent:
    """Вход для оценки: бар входа, направление, исходный стоп/тейк."""

    bar_index: int
    direction: str  # "long" | "short"
    entry_price: float
    initial_stop: float
    take_profit: float | None = None


@dataclass
class ExitMetrics:
    n: int = 0
    wins: int = 0
    expectancy: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_mfe_r: float = 0.0
    avg_mae_r: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "n": float(self.n),
            "expectancy": self.expectancy,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "avg_mfe_r": self.avg_mfe_r,
            "avg_mae_r": self.avg_mae_r,
        }


def _ema(values: Sequence[float], span: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (span + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def _atr(bars: Sequence[BarLike], index: int, period: int = 14) -> float:
    """ATR14 по данным до ``index`` включительно (без lookahead)."""
    start = max(1, index - period + 1)
    trs = []
    for idx in range(start, index + 1):
        h, lo = float(bars[idx].high), float(bars[idx].low)
        pc = float(bars[idx - 1].close)
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def _swing(bars: Sequence[BarLike], index: int, lookback: int, direction: str) -> float | None:
    start = max(0, index - lookback)
    window = bars[start:index]
    if not window:
        return None
    if direction == "long":
        return min(float(b.low) for b in window)
    return max(float(b.high) for b in window)


def _signed_r(direction: str, entry: float, stop: float, price: float) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    move = price - entry if direction == "long" else entry - price
    return move / risk


def _cost_r(direction: str, entry: float, stop: float, exit_price: float,
            fee_pct: float, slippage_pct: float) -> float:
    """Издержки (две стороны) в R: комиссия + slippage от цены."""
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    # Цена-издержка на сторону = entry * (fee+slip) (доли цены), две стороны.
    return 2.0 * (fee_pct + slippage_pct) * entry / risk


def _simulate_one(
    bars: Sequence[BarLike],
    entry: EntryEvent,
    variant: str,
    params: dict[str, Any],
    fee_pct: float,
    slippage_pct: float,
    regimes: Sequence[str] | None = None,
) -> tuple[float, float, float]:
    """Симуляция одного выхода. (exit_r_net, mfe_r, mae_r)."""
    risk = abs(entry.entry_price - entry.initial_stop)
    if risk <= 0:
        return 0.0, 0.0, 0.0
    direction = entry.direction
    initial_stop = entry.initial_stop
    tp = entry.take_profit

    ema_vals = _ema([float(b.close) for b in bars], params.get("ema", 9)) \
        if variant == "MOMENTUM_EXIT" else []
    exit_regimes = set(params.get("exit_regimes", [])) if variant == "REGIME_EXIT" else set()

    mfe_r, mae_r = -9e9, 9e9
    for i in range(entry.bar_index + 1, len(bars)):
        b = bars[i]
        high, low = float(b.high), float(b.low)
        fav = high if direction == "long" else low
        unf = low if direction == "long" else high
        mfe_r = max(mfe_r, _signed_r(direction, entry.entry_price, initial_stop, fav))
        mae_r = min(mae_r, _signed_r(direction, entry.entry_price, initial_stop, unf))
        bars_held = i - entry.bar_index

        # Стоп варианта (может меняться по барам). АТR — только по данным
        # до текущего бара (стоп за бар до, без lookahead).
        stop = initial_stop
        if variant == "ATR_STOP":
            atr = _atr(bars, i - 1)
            if atr > 0:
                stop = (entry.entry_price - params.get("k", 2.0) * atr) if direction == "long" \
                    else entry.entry_price + params.get("k", 2.0) * atr
        elif variant == "STRUCTURE_STOP":
            sw = _swing(bars, i, params.get("lookback", 10), direction)
            if sw is not None:
                stop = sw
        elif variant == "TRAILING":
            atr = _atr(bars, i - 1)
            if atr > 0:
                extreme = max(float(x.close) for x in bars[entry.bar_index + 1:i + 1]) \
                    if direction == "long" else min(float(x.close) for x in bars[entry.bar_index + 1:i + 1])
                stop = extreme - params.get("k", 2.0) * atr if direction == "long" \
                    else extreme + params.get("k", 2.0) * atr
        elif variant == "BREAKEVEN":
            trigger = params.get("trigger_r", 1.0)
            if mfe_r >= trigger:
                stop = max(stop, entry.entry_price) if direction == "long" \
                    else min(stop, entry.entry_price)

        # Вынужденные закрытия (в close бара).
        close = float(b.close)
        if variant == "TIME_STOP" and bars_held >= params.get("bars", 12):
            r = _signed_r(direction, entry.entry_price, initial_stop, close)
            return r - _cost_r(direction, entry.entry_price, initial_stop, close,
                               fee_pct, slippage_pct), mfe_r, mae_r
        if variant == "MOMENTUM_EXIT" and ema_vals:
            crossed_down = direction == "long" and close < ema_vals[i]
            crossed_up = direction == "short" and close > ema_vals[i]
            if crossed_down or crossed_up:
                r = _signed_r(direction, entry.entry_price, initial_stop, close)
                return r - _cost_r(direction, entry.entry_price, initial_stop, close,
                                   fee_pct, slippage_pct), mfe_r, mae_r
        if variant == "REGIME_EXIT" and regimes is not None:
            if i < len(regimes) and regimes[i] in exit_regimes:
                r = _signed_r(direction, entry.entry_price, initial_stop, close)
                return r - _cost_r(direction, entry.entry_price, initial_stop, close,
                                   fee_pct, slippage_pct), mfe_r, mae_r

        # Тейк (для STATIC_TP и остальных — тейк тоже работает).
        if tp is not None:
            tp_hit = (direction == "long" and high >= tp) or (direction == "short" and low <= tp)
            if tp_hit:
                r = _signed_r(direction, entry.entry_price, initial_stop, tp)
                return r - _cost_r(direction, entry.entry_price, initial_stop, tp,
                                   fee_pct, slippage_pct), mfe_r, mae_r

        # Стоп.
        stop_hit = (direction == "long" and low <= stop) or (direction == "short" and high >= stop)
        if stop_hit:
            r = _signed_r(direction, entry.entry_price, initial_stop, stop)
            return r - _cost_r(direction, entry.entry_price, initial_stop, stop,
                               fee_pct, slippage_pct), mfe_r, mae_r

    # Данные кончились — закрываем в последнем close.
    last_close = float(bars[-1].close)
    r = _signed_r(direction, entry.entry_price, initial_stop, last_close)
    return r - _cost_r(direction, entry.entry_price, initial_stop, last_close,
                       fee_pct, slippage_pct), mfe_r, mae_r


def evaluate_exit(
    bars: Sequence[BarLike],
    entries: list[EntryEvent],
    variant: str,
    params: dict[str, Any] | None = None,
    fee_pct: float = 0.0,
    slippage_pct: float = 0.0,
    regimes: Sequence[str] | None = None,
) -> ExitMetrics:
    """Метрики варианта выхода на выборке входов (нет lookahead: только
    бары после входа)."""
    if variant not in EXIT_VARIANTS:
        raise ValueError(f"неизвестный exit-вариант: {variant}")
    p = {**EXIT_VARIANTS[variant]["params"], **(params or {})}
    rs: list[float] = []
    mfes: list[float] = []
    maes: list[float] = []
    for e in entries:
        if e.bar_index + 1 >= len(bars):
            continue
        r, mfe, mae = _simulate_one(bars, e, variant, p, fee_pct, slippage_pct, regimes)
        rs.append(r)
        mfes.append(mfe)
        maes.append(mae)
    n = len(rs)
    if n == 0:
        return ExitMetrics()
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return ExitMetrics(
        n=n,
        wins=len(wins),
        expectancy=sum(rs) / n,
        win_rate=len(wins) / n,
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else (2.0 if gross_win > 0 else 0.0),
        avg_mfe_r=sum(mfes) / n,
        avg_mae_r=sum(maes) / n,
    )


# ---------------------------------------------------------------------------
# Walk-forward без leakage (TZ §16/§17/§21)
# ---------------------------------------------------------------------------

def walk_forward_evaluate(
    bars: Sequence[BarLike],
    entries: list[EntryEvent],
    variant: str,
    params: dict[str, Any] | None = None,
    fee_pct: float = 0.0,
    regimes: Sequence[str] | None = None,
    folds: int = 4,
) -> dict[str, ExitMetrics]:
    """Временная разбивка на ``folds`` блоков (TZ §21, без leakage):

        seg1 = train, seg2..seg_{k-1} = validation, seg_k = OOS,
        walk_forward = средняя expectancy по seg2..seg_k
        (скользящие OOS-окна, вход — только из своего блока,
        выход — строго на более поздних барах).

    Блоки не пересекаются по входам — доказательство каждого периода
    считается на своих данных.
    """
    if folds < 3:
        raise ValueError("folds >= 3 (train + validation + OOS)")
    n = len(bars)
    cut = n // folds

    def seg_entries(f: int) -> list[EntryEvent]:
        lo, hi = cut * (f - 1), cut * f
        return [e for e in entries if lo <= e.bar_index < hi]

    out: dict[str, ExitMetrics] = {
        "train": evaluate_exit(bars, seg_entries(1), variant, params, fee_pct, 0.0, regimes),
        "validation": evaluate_exit(bars, seg_entries(2), variant, params, fee_pct, 0.0, regimes),
        "oos": evaluate_exit(bars, seg_entries(folds), variant, params, fee_pct, 0.0, regimes),
    }
    wf_exps: list[float] = []
    for f in range(2, folds + 1):
        m = evaluate_exit(bars, seg_entries(f), variant, params, fee_pct, 0.0, regimes)
        if m.n:
            wf_exps.append(m.expectancy)
    out["walk_forward"] = ExitMetrics(
        n=len(wf_exps),
        expectancy=sum(wf_exps) / len(wf_exps) if wf_exps else 0.0,
        win_rate=0.0,
    )
    return out


# ---------------------------------------------------------------------------
# Регистрация в HypothesisStore (общая память, TZ §14)
# ---------------------------------------------------------------------------

def exit_hypothesis_id(variant: str, strategy: str, regime: str,
                       params: dict[str, Any]) -> str:
    import hashlib
    import json

    raw = f"exit:{variant}|{strategy}|{regime}|{json.dumps(params, sort_keys=True)}"
    return "exit-" + hashlib.sha1(raw.encode()).hexdigest()[:10]


def register_exit_hypothesis(
    store: Any,
    *,
    variant: str,
    strategy: str,
    regime: str,
    params: dict[str, Any],
    metrics: dict[str, ExitMetrics],
    stress_metrics: dict[str, Any] | None = None,
    min_samples: int = 20,
    lift_vs_baseline: float | None = None,
) -> tuple[str, bool, str]:
    """Создать/обновить гипотезу варианта выхода и попытаться довести до
    VALIDATED (если есть ВСЕ доказательства). Возвращает
    (id, promoted, reason)."""
    from .hypothesis_engine import HypothesisStatus, new_hypothesis

    hid = exit_hypothesis_id(variant, strategy, regime, params)
    existing = store.get(hid)
    if existing is None:
        existing = new_hypothesis(
            id=hid,
            description=f"Exit {variant} params={params} for {strategy} in {regime}",
            strategy_id=strategy,
            features={"exit_variant": variant, "exit_params": params},
            conditions={"exit_variant": variant, "regime": regime},
            timeframes=[],
            market_regimes=[regime] if regime != "ANY" else [],
            sample_size=int(metrics.get("train", ExitMetrics()).n
                            + metrics.get("oos", ExitMetrics()).n),
        )
        store.add(existing)
    train = metrics.get("train", ExitMetrics())
    validation = metrics.get("validation", ExitMetrics())
    oos = metrics.get("oos", ExitMetrics())
    wf = metrics.get("walk_forward", ExitMetrics())
    existing.train_metrics = train.as_dict()
    existing.validation_metrics = validation.as_dict()
    existing.oos_metrics = oos.as_dict()
    existing.walk_forward_metrics = wf.as_dict()
    existing.sample_size = int(
        train.n + validation.n + oos.n
    )
    if stress_metrics is not None:
        existing.stress_metrics = stress_metrics
    existing.expectancy = oos.expectancy
    existing.profit_factor = oos.profit_factor
    existing.win_rate = oos.win_rate
    existing.mfe = oos.avg_mfe_r
    existing.mae = oos.avg_mae_r
    # TZ P0-2: lift vs baseline. Если не указан, считаем от OOS expectancy
    # (baseline для exit research = "без оптимизации выхода" = 0).
    if lift_vs_baseline is not None:
        existing.lift_vs_baseline = lift_vs_baseline
    else:
        existing.lift_vs_baseline = max(0.0, oos.expectancy)

    promoted, reason = False, "not validated"
    if existing.status is HypothesisStatus.DISCOVERED:
        ok, why = store.transition(hid, HypothesisStatus.TESTING)
        if not ok:
            return hid, False, why
    if existing.status is HypothesisStatus.TESTING:
        ok, why = store.transition(hid, HypothesisStatus.VALIDATED, min_samples=min_samples)
        if ok:
            promoted = True
            reason = ""
        else:
            reason = why
    return hid, promoted, reason
