"""Конфигурация цепочки принятия решений."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class DecisionConfig:
    """Параметры Decision Pipeline."""

    # Структура.
    swing_lookback: int = 20

    # Стакан/ликвидность.
    min_book_depth: float = 5000.0
    max_spread_pct: float = 0.15
    # ЕДИНИЦЫ (аудит A5): ПРОЦЕНТЫ (0.05 = 0.05% цены), не доли.
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

    # Meta-Strategy
    min_ev_r: float = 0.05
    ev_shrinkage_k: float = 20.0
    min_ev_samples: int = 30
    min_ev_confidence: float = 0.0

    fee_pct: float = 0.0005
    slippage_pct: float = 0.001
    parameters: dict[str, Any] = field(default_factory=dict)
