"""Гейты входа «не торгуй в шуме» (PR #78, часть 3).

Метод внешнего автора («Зевс», уроки 1–9) прямо запрещает торговать в боковике
и требует, чтобы цель была кратно больше издержек. У бота ни того, ни другого
не было (сверка — ``docs/ZEUS_RECONCILIATION.md``):

* ``STRATEGY_REGIME_COMPATIBILITY`` (``engines/regime_detector.py``) для
  ``LOW_VOLATILITY`` даёт ``ON`` у momentum/mean_reversion/ts_momentum/arbitrage и
  ``REDUCED`` у adaptive_grid — т.е. в тишине входы всегда разрешены;
* цены цикла в R-единицах никем не ограничивены: в логе эпохи-2 медиана
  ``costs_r`` = 0.271R и 60.4% сделок имели ``costs_r >= 0.25``.

Отсюда два гейта:

``A`` (regime)
    не входить, если режим ``LOW_VOLATILITY`` — правило из НАШИХ данных,
    без новых порогов (режим уже считается ``RegimeEngine``-ом).
``B`` (vola-floor)
    не входить, если ожидаемый round-trip стоит >= ``max_costs_r`` от R
    (вычисляется ДО входа из fee/slippage конфига и расстояния до стопа).

Режимы работы (по образцу D5/п.7 — shadow отдельно от живого гейта):

* ``entry_gates_shadow_enabled=True`` (default) — чистая телеметрия: считает,
    пишет наблюдение в NO_TRADE с ``rejection_stage="entry_gate"`` и гипотетический
    R, входи НЕ блокирует. Именно это позволяет оценить включение ДО включения;
* ``entry_gates_enabled=False`` (default) — живой режим: блокирует вход.
    Включается только решением владельца на срезе ~26–27.09 (окно фиксации #76).

Модуль чистый: без I/O, без импортов движка, fail-open при любых дефектах
входных данных (неизвестный режим, нулевой стоп) — сломать вход он не может.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

# Режимы, при которых вход запрещён (гейт A). По умолчанию только LOW_VOLATILITY:
# это единственный режим, по которому у нас есть измеренный аргумент
# (медианный встречный ход за 2 ч 0.28–0.34% при цене цикла 30 б.п.).
DEFAULT_BLOCK_REGIMES: frozenset[str] = frozenset({"LOW_VOLATILITY"})

# Потолок цены цикла, выраженной в R (гейт B). 0.25 = «четверть риска на издержки».
DEFAULT_MAX_COSTS_R = 0.25

# Ключевое слово для поиска в логах (обычай репозитория: HTF_GATE, DECISION, …).
LOG_KEYWORD = "ENTRY_GATE"


@dataclass(frozen=True)
class EntryGateConfig:
    """Снимок конфигурации гейтов (живёт в ``TradingEngineConfig``)."""

    enabled: bool = False
    shadow_enabled: bool = True
    block_regimes: frozenset[str] = field(default_factory=lambda: DEFAULT_BLOCK_REGIMES)
    max_costs_r: float = DEFAULT_MAX_COSTS_R

    @classmethod
    def from_engine_config(cls, cfg: Any) -> EntryGateConfig:
        """Собрать из ``TradingEngineConfig``; отсутствующие поля → дефолты (off)."""
        reg = getattr(cfg, "entry_gate_block_regimes", None) or DEFAULT_BLOCK_REGIMES
        if isinstance(reg, str):
            reg = frozenset(p.strip() for p in reg.split(",") if p.strip())
        else:
            reg = frozenset(str(p) for p in cast_iterable(reg))
        return cls(
            enabled=bool(getattr(cfg, "entry_gates_enabled", False)),
            shadow_enabled=bool(getattr(cfg, "entry_gates_shadow_enabled", True)),
            block_regimes=reg,
            max_costs_r=float(getattr(cfg, "entry_gate_max_costs_r", DEFAULT_MAX_COSTS_R)),
        )

    @property
    def active(self) -> bool:
        """Нужно ли что-то вычислять вообще (живой гейт или тень)."""
        return self.enabled or self.shadow_enabled


def cast_iterable(value: Any) -> Iterable[Any]:
    """Итератор по значению, которое может быть строкой/набором/списком."""
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set, frozenset)):
        return value
    return (value,)


@dataclass(frozen=True)
class EntryGateVerdict:
    """Решение гейтов. ``blocked`` — «сработал запрет»; ``live`` — блокировать ли вход."""

    blocked: bool = False
    live: bool = False
    code: str = ""
    costs_r: float | None = None
    regime: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def tag(self) -> str:
        return "BLOCKED" if (self.blocked and self.live) else ("SHADOW" if self.blocked else "PASS")

    def reasons(self) -> list[str]:
        if not self.blocked:
            return []
        return ["rejection_stage=entry_gate", f"gate={self.code}", f"regime={self.regime}"]

    def log_line(self, symbol: str) -> str:
        cr = "n/a" if self.costs_r is None else f"{self.costs_r:.3f}"
        return (
            f"{LOG_KEYWORD} {self.tag} {symbol} gate={self.code or '-'} "
            f"regime={self.regime or '-'} costs_r={cr} "
            f"max_costs_r={self.details.get('max_costs_r', '-')}"
        )


def pre_entry_costs_r(
    *,
    entry: float,
    stop: float,
    fee_pct: float,
    slippage_pct: float,
) -> float | None:
    """Цена round-trip в R-единицах ДО входа. ``None`` — вычислить нельзя.

    Та же формула, что пишет ``costs_r`` в запись сделки
    (``trading_engine.py``, ``_open_position``), но без подглядывания в факт:
    ``2 × (fee + slippage) / (|entry − stop| / entry)``.
    """
    try:
        entry_f = float(entry)
        stop_f = float(stop)
        if entry_f <= 0.0:
            return None
        stop_pct = abs(entry_f - stop_f) / entry_f
        if stop_pct <= 0.0:
            return None
        return 2.0 * (float(fee_pct) + float(slippage_pct)) / stop_pct
    except (TypeError, ValueError):
        return None


def evaluate(
    *,
    entry: float,
    stop: float,
    regime_info: Mapping[str, Any] | Any,
    fee_pct: float,
    slippage_pct: float,
    cfg: EntryGateConfig,
) -> EntryGateVerdict:
    """Вердикт гейтов. Fail-open: любой шум во входных данных = «не блокировать».

    ``regime_info`` — либо строка режима, либо dict из ``decision.diagnostics``
    (``{"regime": ..., "confidence": ...}``).
    """
    if not cfg.active:
        return EntryGateVerdict()
    try:
        if isinstance(regime_info, Mapping):
            regime = str(regime_info.get("regime") or "")
        else:
            regime = str(regime_info or "")
    except Exception:  # pragma: no cover - защитный путь
        regime = ""

    costs_r = pre_entry_costs_r(entry=entry, stop=stop, fee_pct=fee_pct, slippage_pct=slippage_pct)
    details: dict[str, Any] = {"max_costs_r": cfg.max_costs_r, "block_regimes": sorted(cfg.block_regimes)}

    # Гейт A — режим.
    if regime and regime in cfg.block_regimes:
        return EntryGateVerdict(
            blocked=True, live=cfg.enabled, code="REGIME", costs_r=costs_r,
            regime=regime, details=details,
        )
    # Гейт B — вола-флор: издержки не должны съедать риск.
    if costs_r is not None and costs_r >= cfg.max_costs_r:
        return EntryGateVerdict(
            blocked=True, live=cfg.enabled, code="COSTS_R", costs_r=costs_r,
            regime=regime, details=details,
        )
    return EntryGateVerdict(blocked=False, live=False, costs_r=costs_r, regime=regime, details=details)
