# ruff: noqa: UP042
"""Regime 2.0: три ортогональные оси режима + кросс-маркет контекст (МТЗ §10–14).

Оси (в отличие от единого enum ``MarketRegime`` они ортогональны —
режим = вектор, а не одна метка):

- ``TrendAxis``      — STRONG / WEAK / RANGE / TRANSITION (сила и устойчивость
  направления; BREAKOUT и смена структуры — TRANSITION);
- ``VolatilityAxis`` — VERY_LOW / LOW / NORMAL / HIGH / EXTREME (5 уровней
  по ATR% ; границы согласованы с legacy-порогами RegimeEngine 1.5/5/10);
- ``LiquidityAxis``  — THIN / NORMAL / DEEP / STRESSED (спред и глубина
  стакана; STRESSED — спред кратный максимуму, «стакан в шоке»).

Ключ бакета статистики ``axes_key()`` — детерминированная строка без
разделителя хранилища ("|"). Кросс-маркет (BTC/ETH/SOL относительная сила)
в бакет НЕ входит (иначе выборка распылится): это фичи/контекст в ``cross``.

Миграция (МТЗ: «старые бакеты не ломать»): StrategyStatsStore пишет и читает
одновременно новый композитный ключ и legacy-значение ``MarketRegime``;
при пустом новом — фолбэк на старый, затем ANY (см. strategy_stats).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TrendAxis(str, Enum):
    STRONG = "STRONG"
    WEAK = "WEAK"
    RANGE = "RANGE"
    TRANSITION = "TRANSITION"


class VolatilityAxis(str, Enum):
    VERY_LOW = "VERY_LOW"
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class LiquidityAxis(str, Enum):
    THIN = "THIN"
    NORMAL = "NORMAL"
    DEEP = "DEEP"
    STRESSED = "STRESSED"


# Пороги осей (atr_pct в % от цены). 1.5 / 5 / 10 — исторические пороги
# RegimeEngine (LOW_VOL/HIGH_VOL/PANIC), чтобы оси и legacy-режим не
# противоречили друг другу.
ATR_VERY_LOW = 0.5
ATR_LOW = 1.5
ATR_HIGH = 5.0
ATR_EXTREME = 10.0


@dataclass
class CrossMarketContext:
    """Относительная сила инструмента против BTC + режимы majors (МТЗ §13)."""

    btc_change_pct_24h: float | None = None
    eth_change_pct_24h: float | None = None
    sol_change_pct_24h: float | None = None
    symbol_change_pct_24h: float | None = None
    btc_regime: str | None = None
    # RS = динамика инструмента минус динамика BTC (п.п. за 24ч).
    relative_strength_pct: float | None = None
    rs_bucket: str = "UNKNOWN"  # OUTPERFORM / NEUTRAL / UNDERPERFORM
    majors_risk_off: bool = False  # BTC в PANIC/BEAR — фон «против лонгов»

    def to_dict(self) -> dict[str, Any]:
        return {
            "btc_change_pct_24h": self.btc_change_pct_24h,
            "eth_change_pct_24h": self.eth_change_pct_24h,
            "sol_change_pct_24h": self.sol_change_pct_24h,
            "symbol_change_pct_24h": self.symbol_change_pct_24h,
            "btc_regime": self.btc_regime,
            "relative_strength_pct": self.relative_strength_pct,
            "rs_bucket": self.rs_bucket,
            "majors_risk_off": self.majors_risk_off,
        }

    @classmethod
    def from_global_market(cls, gm: dict[str, Any] | None) -> CrossMarketContext | None:
        """Собрать контекст из ``ctx.global_market`` (ключи опциональны).

        Пустой/безизвестный словарь -> None: оси строятся без кросс-части,
        поведение побайтово как до A2.
        """
        if not gm:
            return None
        def num(key: str) -> float | None:
            v = gm.get(key)
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        btc = num("btc_change_pct_24h")
        eth = num("eth_change_pct_24h")
        sol = num("sol_change_pct_24h")
        sym = num("symbol_change_pct_24h")
        rs = (sym - btc) if (sym is not None and btc is not None) else None
        if rs is None:
            bucket = "UNKNOWN"
        elif rs > 1.0:
            bucket = "OUTPERFORM"
        elif rs < -1.0:
            bucket = "UNDERPERFORM"
        else:
            bucket = "NEUTRAL"
        btc_regime = gm.get("btc_regime")
        risk_off = str(btc_regime or "") in {"PANIC", "HIGH_VOLATILITY", "STRONG_BEAR_TREND"}
        if all(x is None for x in (btc, eth, sol, sym)) and btc_regime is None:
            return None
        return cls(
            btc_change_pct_24h=btc,
            eth_change_pct_24h=eth,
            sol_change_pct_24h=sol,
            symbol_change_pct_24h=sym,
            btc_regime=str(btc_regime) if btc_regime is not None else None,
            relative_strength_pct=round(rs, 3) if rs is not None else None,
            rs_bucket=bucket,
            majors_risk_off=risk_off,
        )


@dataclass
class RegimeAxes:
    """Вектор режима: три оси + кросс-маркет-контекст."""

    trend: TrendAxis = TrendAxis.RANGE
    volatility: VolatilityAxis = VolatilityAxis.NORMAL
    liquidity: LiquidityAxis = LiquidityAxis.NORMAL
    cross: CrossMarketContext | None = None
    # Диагностические числа, из которых выведены оси.
    inputs: dict[str, Any] = field(default_factory=dict)

    def axes_key(self) -> str:
        """Композитный ключ бакета (стабильный, без '|')."""
        return f"T:{self.trend.value}/V:{self.volatility.value}/L:{self.liquidity.value}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "trend": self.trend.value,
            "volatility": self.volatility.value,
            "liquidity": self.liquidity.value,
            "axes_key": self.axes_key(),
            "cross": self.cross.to_dict() if self.cross else None,
        }


def trend_axis(
    *,
    adx: float,
    aligned_bull: bool,
    aligned_bear: bool,
    breakout: bool = False,
    adx_threshold: float = 23.0,
    adx_strong: float = 40.0,
) -> TrendAxis:
    if breakout:
        # Пробой структуры на объёме — режим неустойчив, доверять
        # «выровненной» картине рано.
        return TrendAxis.TRANSITION
    aligned = aligned_bull or aligned_bear
    if aligned and adx >= adx_strong:
        return TrendAxis.STRONG
    if aligned and adx >= adx_threshold:
        return TrendAxis.WEAK
    if not aligned and adx >= adx_threshold:
        # ADX высокий, но EMA-структура не выровнена — смена тренда.
        return TrendAxis.TRANSITION
    return TrendAxis.RANGE


def volatility_axis(*, atr_pct: float) -> VolatilityAxis:
    if atr_pct < ATR_VERY_LOW:
        return VolatilityAxis.VERY_LOW
    if atr_pct < ATR_LOW:
        return VolatilityAxis.LOW
    if atr_pct < ATR_HIGH:
        return VolatilityAxis.NORMAL
    if atr_pct < ATR_EXTREME:
        return VolatilityAxis.HIGH
    return VolatilityAxis.EXTREME


def liquidity_axis(
    *,
    spread_pct: float | None,
    depth_usd: float | None,
    vol_spike: float = 1.0,
    min_depth: float = 5000.0,
    max_spread_pct: float = 0.15,
) -> LiquidityAxis:
    """Приоритет: STRESSED > THIN > DEEP > NORMAL; нет данных — NORMAL."""
    if spread_pct is None and depth_usd is None:
        return LiquidityAxis.NORMAL
    stressed = (
        (spread_pct is not None and spread_pct >= 2.0 * max_spread_pct)
        or vol_spike >= 3.0
    )
    if stressed:
        return LiquidityAxis.STRESSED
    if depth_usd is not None and depth_usd < min_depth:
        return LiquidityAxis.THIN
    if (
        depth_usd is not None
        and depth_usd >= 3.0 * min_depth
        and spread_pct is not None
        and spread_pct <= 0.5 * max_spread_pct
    ):
        return LiquidityAxis.DEEP
    return LiquidityAxis.NORMAL


def derive_axes(
    *,
    adx: float = 0.0,
    aligned_bull: bool = False,
    aligned_bear: bool = False,
    breakout: bool = False,
    atr_pct: float = 0.0,
    vol_spike: float = 1.0,
    spread_pct: float | None = None,
    depth_usd: float | None = None,
    min_depth: float = 5000.0,
    max_spread_pct: float = 0.15,
    adx_threshold: float = 23.0,
    adx_strong: float = 40.0,
    cross: CrossMarketContext | None = None,
    inputs: dict[str, Any] | None = None,
) -> RegimeAxes:
    """Чистая классификация осей из готовых сигналов (для тестов и reuse)."""
    return RegimeAxes(
        trend=trend_axis(
            adx=adx,
            aligned_bull=aligned_bull,
            aligned_bear=aligned_bear,
            breakout=breakout,
            adx_threshold=adx_threshold,
            adx_strong=adx_strong,
        ),
        volatility=volatility_axis(atr_pct=atr_pct),
        liquidity=liquidity_axis(
            spread_pct=spread_pct,
            depth_usd=depth_usd,
            vol_spike=vol_spike,
            min_depth=min_depth,
            max_spread_pct=max_spread_pct,
        ),
        cross=cross,
        inputs=inputs or {},
    )


def orderbook_liquidity_inputs(
    orderbook: Any, current_price: float
) -> tuple[float | None, float | None]:
    """(spread_pct, depth_usd) по top-20 уровней с каждой стороны.

    Адаптерный стакан (models.OrderBook):Decimal → float; пустой/отсут-
    ствующий стакан — (None, None), ось liquidity остаётся NORMAL.
    """
    if orderbook is None or not current_price:
        return None, None
    try:
        bids = list(getattr(orderbook, "bids", []) or [])[:20]
        asks = list(getattr(orderbook, "asks", []) or [])[:20]
        if not bids or not asks:
            return None, None
        best_bid = float(bids[0].price)
        best_ask = float(asks[0].price)
        spread_pct = (best_ask - best_bid) / float(current_price) * 100.0
        depth = sum(float(e.quantity) * float(e.price) for e in bids) + sum(
            float(e.quantity) * float(e.price) for e in asks
        )
        return round(spread_pct, 4), round(depth, 2)
    except Exception:
        return None, None
