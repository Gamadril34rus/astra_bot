"""Конфигурация цепочки принятия решений."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class DecisionConfig:
    """Пороги и веса для пайплайна.

    Все числовые параметры подобраны как стартовые и должны
    калиброваться по out-of-sample бэктестам.
    """

    # Доступные таймфреймы и их назначение.
    timeframes: tuple[str, ...] = ("4h", "1h", "15m", "5m")

    # Тренд.
    adx_trend_threshold: float = 23.0
    adx_strong_threshold: float = 40.0
    ema_fast: int = 20
    ema_mid: int = 50
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
    min_rr: float = 1.5

    # ML/EV.
    min_ml_probability: float = 0.60
    min_expected_edge_pct: float = 0.4  # %

    # Meta-Strategy: выбор стратегии по EV в текущем режиме (TZ §5/§6).
    # min_ev_r — минимальный shrunken EV (в R); отрицательный EV всегда
    # блокирует. min_ev_confidence — порог надёжности при достаточной
    # выборке. min_ev_samples — от какой выборки включается confidence-гейт.
    # ev_shrinkage_k — сила bayesian shrinkage к prior (n/(n+k) вес).
    min_ev_r: float = 0.05
    min_ev_confidence: float = 0.3
    min_ev_samples: int = 30
    ev_shrinkage_k: float = 30.0

    # Веса для скоров.
    score_weights: dict[str, float] = field(
        default_factory=lambda: {
            "trend": 18,
            "momentum": 12,
            "volume": 10,
            "structure": 15,
            "liquidity": 10,
            "order_book": 5,
            "news": 8,
            "onchain": 4,
            "derivatives": 3,
            "correlation": 7,
            "ml": 12,
        }
    )
