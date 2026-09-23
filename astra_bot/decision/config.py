"""Конфигурация цепочки принятия решений."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class DecisionConfig:
    """Пороги и веса для пайплайна.

    Все числовые параметры подобраны как стартовые и должны
    калиброваться по out-of-sample бэктестам.

    Контракт ``confidence`` (D3, 13.09.2026): уверенность стратегии/скора —
    **НЕ калиброванная вероятность** прибыли. Она выбирает ступень плеча
    (``LEVERAGE_LADDER``) и подставляется вместо ML-вероятности, когда
    модели нет (`pipeline.py`); в сайзинге — только множитель 0.5x–1.0x.
    Калибровка (Brier/reliability) — отдельная задача: числа confidence
    на неё не претендуют и не пересчитываются в рамках D3.
    """

    # Доступные таймфреймы и их назначение.
    timeframes: tuple[str, ...] = ("4h", "1h", "15m", "5m")

    # Тренд.
    adx_trend_threshold: float = 23.0
    adx_strong_threshold: float = 40.0
    ema_fast: int = 20
    ema_mid: int = 50
    ema_band: int = 100
    ema_slow: int = 200

    # Волатильность.
    atr_period: int = 14
    high_volatility_atr_pct: float = 4.0
    extreme_volatility_atr_pct: float = 7.0

    # Bollinger Bands.
    bb_period: int = 20
    bb_std: float = 2.0

    # Объём.
    volume_spike_factor: float = 1.5
    volume_period: int = 20

    # Структура.
    swing_lookback: int = 20

    # Стакан/ликвидность.
    min_book_depth: float = 5000.0
    max_spread_pct: float = 0.15
    slippage_buffer_pct: float = 0.05

    # Risk.
    risk_per_trade_pct: Decimal = Decimal("0.005")
    max_exposure_pct: Decimal = Decimal("0.30")
    max_correlation_exposure: int = 3
    max_daily_loss_pct: float = 3.0
    max_drawdown_pct: float = 15.0
    min_rr: float = 3.0

    # D5 фаза 0: HTF directional-фильтр — SHADOW
    htf_shadow_enabled: bool = True
    htf_shadow_tf: str = "4h"
    htf_shadow_min_closed_bars: int = 60
    htf_shadow_log_path: str = "models/htf_shadow_bans.jsonl"
    htf_gate_enabled: bool = True
    htf_gate_tf: str = "1d"
    htf_gate_min_bars: int = 200
    htf_gate_strategies: frozenset[str] = frozenset(
        {"ob_swing", "breaker_block", "maicross", "rounded_top", "rounded_bottom"}
    )
    htf_gate_btc_symbol: str = "BTC-USDT"
    htf_gate_btc_cache_ttl: int = 900
    htf_gate_daily_bars: int = 500

    htf_flip_strategies: frozenset[str] = frozenset(
        {"ts_momentum", "ts_momentum_cross"}
    )

    # ML/EV.
    min_ml_probability: float = 0.60
    min_expected_edge_pct: float = 0.4

    # Meta-Strategy: выбор стратегии по EV в текущем режиме (TZ §5/§6).
    min_ev_r: float = 0.0
    min_ev_samples: int = 20
    min_ev_confidence: float = 0.55
    ev_shrinkage_k: float = 20.0

    # Веса скоринга.
    weight_trend: float = 0.25
    weight_structure: float = 0.20
    weight_momentum: float = 0.15
    weight_volume: float = 0.10
    weight_volatility: float = 0.10
    weight_orderbook: float = 0.10
    weight_ml: float = 0.10
