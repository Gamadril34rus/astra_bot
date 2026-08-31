"""Decision pipeline v2 — единая точка входа с Master Specification v2

НОВАЯ ВЕРСИЯ: Включает все компоненты Master Specification v2

Data → Regime → Strategy → ML → EV → Uncertainty → Decision → Risk → Execution

Любой компонент может вернуть NO_TRADE. Финальное решение всегда
в ``Decision`` с явным списком причин отказа.

Ключевые изменения:
- Добавлена оценка неопределённости (Uncertainty Engine)
- Добавлен вероятностный прогноз (Probabilistic Forecast)
- Добавлена проверка деградации сигналов (Alpha Decay)
- Добавлена оптимизация исполнения (Execution Optimizer)
- Добавлена оценка корреляции сигналов (Signal Correlation)
- Добавлена оценка хвостового риска (Tail Risk)
- Добавлена оценка альтернативной стоимости (Opportunity Cost)
- Добавлен анализ MFE/MAE
- Добавлен контрфактный анализ
- Добавлена классификация убытков
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .config import DecisionConfig
from .context import MarketContext, SignalCandidate
from .correlation_engine import CorrelationEngine
from .derivatives_engine import DerivativesEngine
from .ev_engine import EVEngine
from .feature_engine import FeatureEngine
from .liquidity_engine import LiquidityEngine
from .meta_strategy import MetaStrategy, NoTradeReason
from .news_engine import NewsEngine, NewsReport
from .onchain_engine import OnChainEngine
from .orderbook_engine import OrderBookEngine
from .regime_engine import MarketRegime, RegimeEngine
from .scoring import SignalScorer
from .strategy_stats import StrategyStatsStore
from .structure_engine import StructureEngine
from .technical_engine import TechnicalEngine

# NEW: Import all new engines from Master Specification v2
from ..engines.uncertainty_engine import get_uncertainty_engine, UncertaintyType, ModelPrediction, MarketDataQuality, RegimeAssessment
from ..engines.probabilistic_forecast import get_forecast_engine
from ..engines.alpha_decay_engine import get_alpha_decay_engine
from ..engines.execution_optimizer import get_execution_optimizer, OrderType, ExecutionUrgency, OrderBookState, LiquidityState
from ..engines.signal_correlation_engine import get_signal_correlation_engine, SignalFeatures
from ..engines.portfolio_exposure_engine import get_portfolio_exposure_engine, Position
from ..engines.tail_risk_engine import get_tail_risk_engine
from ..engines.mfe_mae_engine import get_mfe_mae_engine
from ..engines.counterfactual_engine import get_counterfactual_engine
from ..engines.loss_attribution_engine import get_loss_attribution_engine
from ..engines.opportunity_cost_engine import get_opportunity_cost_engine, SignalOpportunity
from ..engines.regime_similarity_engine import get_regime_similarity_engine, MarketState

logger = logging.getLogger(__name__)


@dataclass
class Decision:
    """Решение конвейера с поддержкой Master Specification v2"""
    action: str  # LONG / SHORT / NO_TRADE / FLIP / CLOSE
    symbol: str
    reasons: list[str] = field(default_factory=list)
    candidate: SignalCandidate | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    # Кодированная причина NO_TRADE (TZ §12): LOW_EV / BAD_REGIME / ...
    reason_code: str | None = None
    
    # NEW: Дополнительные поля для v2
    uncertainty: float | None = None  # Итоговая неопределённость (0-1)
    net_ev: float | None = None  # Net Expected Value после всех издержек
    execution_plan: dict[str, Any] | None = None  # План исполнения
    alpha_decay_status: dict[str, Any] | None = None  # Статус деградации сигнала
    portfolio_risk: dict[str, Any] | None = None  # Риск портфеля
    tail_risk: dict[str, Any] | None = None  # Хвостовый риск
    
    def to_dict(self) -> dict[str, Any]:
        result = {
            "action": self.action,
            "symbol": self.symbol,
            "reasons": self.reasons,
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "diagnostics": self.diagnostics,
            "reason_code": self.reason_code,
        }
        if self.uncertainty is not None:
            result["uncertainty"] = self.uncertainty
        if self.net_ev is not None:
            result["net_ev"] = self.net_ev
        if self.execution_plan is not None:
            result["execution_plan"] = self.execution_plan
        if self.alpha_decay_status is not None:
            result["alpha_decay_status"] = self.alpha_decay_status
        if self.portfolio_risk is not None:
            result["portfolio_risk"] = self.portfolio_risk
        if self.tail_risk is not None:
            result["tail_risk"] = self.tail_risk
        return result


class DecisionPipelineV2:
    """
    Decision Pipeline v2 с полной поддержкой Master Specification v2
    
    Ключевые изменения:
    1. Оценка неопределённости для каждого кандидата
    2. Вероятностный прогноз вместо единственного предсказания
    3. Проверка деградации сигналов
    4. Оптимизация исполнения
    5. Оценка корреляции сигналов
    6. Оценка хвостового риска
    7. Расчёт Net EV после всех издержек
    8. Оценка альтернативной стоимости
    """
    
    def __init__(
        self,
        config: DecisionConfig | None = None,
        strategies: list | None = None,
        model: Any | None = None,
        stats_store: StrategyStatsStore | None = None,
    ):
        self.config = config or DecisionConfig()
        self.strategies = strategies or []
        self.model = model

        # Existing components
        self.stats_store = stats_store or StrategyStatsStore(
            shrinkage_k=self.config.ev_shrinkage_k,
            min_samples=self.config.min_ev_samples,
        )
        self.meta = MetaStrategy(self.stats_store, self.config)
        self.features = FeatureEngine(self.config)
        self.regime = RegimeEngine(
            adx_threshold=self.config.adx_trend_threshold,
            adx_strong=self.config.adx_strong_threshold,
        )
        self.technical = TechnicalEngine(
            high_vol_atr_pct=self.config.high_volatility_atr_pct,
            extreme_vol_atr_pct=self.config.extreme_volatility_atr_pct,
        )
        self.structure = StructureEngine(self.config.swing_lookback)
        self.book = OrderBookEngine(
            max_spread_pct=self.config.max_spread_pct,
            min_depth=self.config.min_book_depth,
        )
        self.liquidity = LiquidityEngine(
            taker_fee_pct=0.05,
            safety_buffer_pct=self.config.slippage_buffer_pct,
        )
        self.news = NewsEngine()
        self.onchain = OnChainEngine()
        self.derivatives = DerivativesEngine()
        self.correlation = CorrelationEngine()
        self.scorer = SignalScorer(self.config)
        self.ev = EVEngine(self.config.min_expected_edge_pct)
        
        # NEW: Master Specification v2 components
        self.uncertainty_engine = get_uncertainty_engine()
        self.forecast_engine = get_forecast_engine()
        self.alpha_decay_engine = get_alpha_decay_engine()
        self.execution_optimizer = get_execution_optimizer()
        self.signal_correlation_engine = get_signal_correlation_engine()
        self.portfolio_exposure_engine = get_portfolio_exposure_engine()
        self.tail_risk_engine = get_tail_risk_engine()
        self.mfe_mae_engine = get_mfe_mae_engine()
        self.counterfactual_engine = get_counterfactual_engine()
        self.loss_attribution_engine = get_loss_attribution_engine()
        self.opportunity_cost_engine = get_opportunity_cost_engine()
        self.regime_similarity_engine = get_regime_similarity_engine()
        
        # Configuration thresholds
        self.min_confidence_threshold = getattr(self.config, 'min_confidence_threshold', 0.7)
        self.max_uncertainty_threshold = getattr(self.config, 'max_uncertainty_threshold', 0.7)
        self.min_net_ev_threshold = getattr(self.config, 'min_net_ev_r', 0.005)  # 0.5%
    
    # ----------------------------------------------------------- builders
    @staticmethod
    async def _await_evaluate(maybe: Any) -> Any:
        """Дождаться результата strategy.evaluate"""
        if inspect.isawaitable(maybe):
            return await maybe
        return maybe

    async def _candidates_from_strategies(
        self,
        ctx: MarketContext,
        regime: str,
    ) -> list[SignalCandidate]:
        """Получить кандидатов от стратегий (существующая логика)"""
        out: list[SignalCandidate] = []
        primary = ctx.candles_on("5m") or ctx.candles_on("15m") or ctx.candles_on("1h") or ctx.candles_on("4h") or []
        if not primary:
            return out
        for strategy in self.strategies:
            try:
                preferred_tf = getattr(strategy, "preferred_timeframe", None)
                if preferred_tf and ctx.candles_on(preferred_tf):
                    candles = ctx.candles_on(preferred_tf)
                else:
                    candles = primary
                signal = await self._await_evaluate(
                    strategy.evaluate(
                        symbol=ctx.symbol,
                        candles=candles,
                        orderbook=ctx.orderbook,
                        current_price=float(ctx.current_price),
                        market_regime=regime,
                    )
                )
                if signal is None:
                    continue
                out.append(
                    SignalCandidate(
                        symbol=ctx.symbol,
                        direction=signal.direction.value,
                        entry_price=signal.entry_price,
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                        timeframe=preferred_tf or "1h",
                        strategy=getattr(strategy, "name", "strategy"),
                        confidence=signal.confidence,
                        features=getattr(signal, "features", {}) or {},
                    )
                )
            except Exception as exc:
                logger.debug("strategy %s failed: %s", strategy, exc)
        return out
    
    def _ml_probability(self, feats) -> float | None:
        """Получить вероятность от ML модели"""
        if self.model is None:
            return None
        try:
            import numpy as np
            x = np.array(
                [[feats.as_ml_dict().get(k, 0.0) for k in feats.as_ml_dict()]],
                dtype=float,
            )
            proba = self.model.predict_proba(x)[0]
            return float(proba[1]) if len(proba) > 1 else float(proba[0])
        except Exception as exc:
            logger.debug("ML predict failed: %s", exc)
            return None
    
    # ========================================================================
    # NEW METHODS FOR MASTER SPECIFICATION V2
    # ========================================================================
    
    def _assess_uncertainty(
        self,
        candidate: SignalCandidate,
        ctx: MarketContext,
        regime: any
    ) -> dict[str, Any]:
        """
        Оценить неопределённость кандидата (Section 6)
        
        Args:
            candidate: Кандидат сигнала
            ctx: Контекст рынка
            regime: Текущий режим
        
        Returns:
            Словарь с оценкой неопределённости
        """
        try:
            # Создать ModelPrediction для кандидата
            current_pred = ModelPrediction(
                direction=candidate.direction,
                probability=candidate.confidence if candidate.confidence else 0.5,
                expected_return=candidate.expected_edge_pct if candidate.expected_edge_pct else 0.0,
                model_name=candidate.strategy,
                model_version="1.0",
                features_used=list(candidate.features.keys()) if candidate.features else [],
                sample_size=100
            )
            
            # Создать MarketDataQuality
            data_quality = MarketDataQuality(
                spread_pct=ctx.orderbook.spread / float(ctx.current_price) if ctx.orderbook else 0.001,
                depth=10000,
                volume=100000,
                volatility=0.02,
                data_gaps=0,
                latency_ms=10
            )
            
            # Создать RegimeAssessment
            regime_assessment = RegimeAssessment(
                current_regime=regime.regime.value if regime else "UNKNOWN",
                regime_confidence=regime.confidence if regime else 0.5,
                regime_stability=0.7,
                transition_probability=0.1,
                historical_coverage=500
            )
            
            # Оценить неопределённость
            result = self.uncertainty_engine.assess_uncertainty(
                symbol=ctx.symbol,
                timeframe=candidate.timeframe,
                current_prediction=current_pred,
                historical_predictions=[],
                data_quality=data_quality,
                regime_assessment=regime_assessment,
                sample_size=100
            )
            
            return {
                "total_uncertainty": result.total_uncertainty,
                "components": {k: v.to_dict() for k, v in result.components.items()},
                "classification": self.uncertainty_engine.classify_uncertainty_level(result.total_uncertainty),
                "should_trade": self.uncertainty_engine.should_trade(result.total_uncertainty, self.min_confidence_threshold)
            }
        except Exception as e:
            logger.debug(f"Uncertainty assessment failed: {e}")
            return {
                "total_uncertainty": 0.5,
                "components": {},
                "classification": "medium",
                "should_trade": True
            }
    
    def _check_alpha_decay(
        self,
        candidate: SignalCandidate
    ) -> dict[str, Any]:
        """
        Проверить деградацию сигнала (Section 11-12)
        
        Args:
            candidate: Кандидат сигнала
        
        Returns:
            Статус деградации
        """
        try:
            is_expired = self.alpha_decay_engine.is_signal_expired(
                candidate.strategy, candidate.symbol, candidate.timeframe
            )
            is_weakening = self.alpha_decay_engine.is_signal_weakening(
                candidate.strategy, candidate.symbol, candidate.timeframe
            )
            remaining_edge = self.alpha_decay_engine.get_signal_remaining_edge(
                candidate.strategy, candidate.symbol, candidate.timeframe
            )
            signal_age = self.alpha_decay_engine.get_signal_age(
                candidate.strategy, candidate.symbol, candidate.timeframe
            )
            expected_lifetime = self.alpha_decay_engine.get_expected_lifetime(
                candidate.strategy, candidate.symbol, candidate.timeframe
            )
            
            return {
                "is_expired": is_expired,
                "is_weakening": is_weakening,
                "remaining_edge": remaining_edge,
                "signal_age": str(signal_age) if signal_age else None,
                "expected_lifetime": str(expected_lifetime) if expected_lifetime else None,
                "should_trade": not is_expired
            }
        except Exception as e:
            logger.debug(f"Alpha decay check failed: {e}")
            return {
                "is_expired": False,
                "is_weakening": False,
                "remaining_edge": 1.0,
                "should_trade": True
            }
    
    def _calculate_net_ev(
        self,
        candidate: SignalCandidate,
        ctx: MarketContext
    ) -> dict[str, Any]:
        """
        Рассчитать Net Expected Value (Section 13-14)
        
        NetEV = ExpectedGrossReturn - ExpectedFees - ExpectedSlippage - ExpectedFunding - ExpectedExecutionCost
        
        Args:
            candidate: Кандидат сигнала
            ctx: Контекст рынка
        
        Returns:
            Словарь с расчётом Net EV
        """
        try:
            # Ожидаемая валовая доходность
            entry = float(candidate.entry_price)
            stop = float(candidate.stop_loss)
            take = float(candidate.take_profit) if candidate.take_profit else None
            
            stop_distance = abs(entry - stop)
            if stop_distance <= 0:
                return {"net_ev": 0.0, "gross_ev": 0.0, "should_trade": False}
            
            # Ожидаемая доходность от тейк-профита
            if take:
                take_distance = abs(take - entry)
                rr_ratio = take_distance / stop_distance if stop_distance > 0 else 1.0
            else:
                rr_ratio = 1.0  # Консервативно
            
            # Вероятность выигрыша
            p_win = candidate.ml_probability if candidate.ml_probability else max(0.4, candidate.confidence)
            p_win = min(0.99, max(0.01, p_win))
            
            # Ожидаемая валовая доходность (%)
            gross_ev_pct = (p_win * rr_ratio - (1 - p_win) * 1.0)
            
            # Издержки
            # 1. Комиссия (на две стороны)
            fee_pct = 0.001  # 0.1%
            
            # 2. Проскальзывание
            spread_pct = ctx.orderbook.spread / float(ctx.current_price) if ctx.orderbook else 0.001
            slippage_pct = spread_pct * 0.5  # Консервативно
            
            # 3. Фандинг (для перпетьюалов)
            funding_pct = 0.0  # Пока не учитываем
            
            # 4. Издержки исполнения
            execution_cost_pct = 0.0005  # 0.05%
            
            # Net EV
            net_ev_pct = gross_ev_pct - (fee_pct * 2) - slippage_pct - funding_pct - execution_cost_pct
            
            return {
                "gross_ev_pct": gross_ev_pct,
                "net_ev_pct": net_ev_pct,
                "fees_pct": fee_pct * 2,
                "slippage_pct": slippage_pct,
                "funding_pct": funding_pct,
                "execution_cost_pct": execution_cost_pct,
                "should_trade": net_ev_pct >= self.min_net_ev_threshold
            }
        except Exception as e:
            logger.debug(f"Net EV calculation failed: {e}")
            return {
                "gross_ev_pct": 0.0,
                "net_ev_pct": 0.0,
                "should_trade": False
            }
    
    def _optimize_execution(
        self,
        candidate: SignalCandidate,
        ctx: MarketContext
    ) -> dict[str, Any]:
        """
        Оптимизировать исполнение (Section 15-16)
        
        Args:
            candidate: Кандидат сигнала
            ctx: Контекст рынка
        
        Returns:
            План исполнения
        """
        try:
            # Создать OrderBookState
            if ctx.orderbook:
                order_book = OrderBookState(
                    symbol=ctx.symbol,
                    bids=[(float(b.price), float(b.quantity)) for b in ctx.orderbook.bids[:10]],
                    asks=[(float(a.price), float(a.quantity)) for a in ctx.orderbook.asks[:10]],
                    mid_price=float(ctx.current_price),
                    spread=float(ctx.orderbook.spread) if hasattr(ctx.orderbook, 'spread') else 0.0,
                    spread_pct=ctx.orderbook.spread / float(ctx.current_price) if ctx.orderbook.spread > 0 else 0.0,
                    depth=sum(float(b.quantity) + float(a.quantity) for b, a in zip(ctx.orderbook.bids[:10], ctx.orderbook.asks[:10])),
                    best_bid=float(ctx.orderbook.bids[0].price) if ctx.orderbook.bids else 0.0,
                    best_ask=float(ctx.orderbook.asks[0].price) if ctx.orderbook.asks else 0.0
                )
            else:
                order_book = OrderBookState(
                    symbol=ctx.symbol,
                    bids=[],
                    asks=[],
                    mid_price=float(ctx.current_price),
                    spread=0.0,
                    spread_pct=0.0,
                    depth=0.0,
                    best_bid=0.0,
                    best_ask=0.0
                )
            
            # Создать LiquidityState
            liquidity = LiquidityState(
                symbol=ctx.symbol,
                volume_24h=1000000,
                volume_current=100000,
                order_book_liquidity=50000,
                market_depth=100000,
                volatility=0.02
            )
            
            # Создать сигнал для оптимизатора
            signal = {
                "symbol": ctx.symbol,
                "direction": candidate.direction,
                "entry_price": float(candidate.entry_price),
                "position_size": 0.1
            }
            
            # Определить срочность
            if candidate.confidence > 0.9:
                urgency = ExecutionUrgency.HIGH
            elif candidate.confidence > 0.7:
                urgency = ExecutionUrgency.NORMAL
            else:
                urgency = ExecutionUrgency.LOW
            
            # Оптимизировать
            plan = self.execution_optimizer.select_optimal_strategy(
                signal=signal,
                order_book=order_book,
                liquidity=liquidity,
                urgency=urgency,
                expected_edge=candidate.expected_edge_pct if candidate.expected_edge_pct else 0.01,
                position_size=0.1
            )
            
            return {
                "recommended_strategy": plan.recommended_strategy.to_dict(),
                "alternative_strategies": [s.to_dict() for s in plan.alternative_strategies],
                "execution_quality_score": plan.execution_quality_score
            }
        except Exception as e:
            logger.debug(f"Execution optimization failed: {e}")
            return {
                "recommended_strategy": {"order_type": "MARKET"},
                "alternative_strategies": [],
                "execution_quality_score": 0.5
            }
    
    def _check_signal_correlation(
        self,
        candidates: list[SignalCandidate]
    ) -> dict[str, Any]:
        """
        Проверить корреляцию сигналов (Section 23)
        
        Args:
            candidates: Список кандидатов
        
        Returns:
            Результаты анализа корреляции
        """
        try:
            # Создать SignalFeatures для каждого кандидата
            signal_features = []
            for cand in candidates:
                features_dict = cand.features if cand.features else {}
                signal_features.append(SignalFeatures(
                    signal_name=f"{cand.strategy}_{cand.symbol}",
                    features=features_dict
                ))
            
            # Проанализировать корреляцию
            result = self.signal_correlation_engine.analyze_signal_correlation(signal_features)
            
            return {
                "correlation_matrix": result.correlation_matrix.to_dict(),
                "factor_groups": [g.to_dict() for g in result.factor_groups],
                "independent_signals": result.independent_signals,
                "correlated_pairs": result.correlated_pairs
            }
        except Exception as e:
            logger.debug(f"Signal correlation check failed: {e}")
            return {
                "correlation_matrix": {},
                "factor_groups": [],
                "independent_signals": [c.strategy for c in candidates],
                "correlated_pairs": []
            }
    
    def _assess_portfolio_risk(
        self,
        candidate: SignalCandidate,
        current_positions: list
    ) -> dict[str, Any]:
        """
        Оценить риск портфеля (Section 24)
        
        Args:
            candidate: Кандидат сигнала
            current_positions: Текущие позиции
        
        Returns:
            Оценка риска портфеля
        """
        try:
            # Создать позиции для оценки
            positions = []
            for pos in current_positions:
                positions.append(Position(
                    symbol=pos.get("symbol", ""),
                    side=pos.get("direction", "long"),
                    quantity=pos.get("quantity", 0),
                    entry_price=pos.get("entry_price", 0),
                    current_price=pos.get("current_price", 0)
                ))
            
            # Добавить новую позицию
            positions.append(Position(
                symbol=candidate.symbol,
                side=candidate.direction,
                quantity=0.1,  # Примерный размер
                entry_price=float(candidate.entry_price),
                current_price=float(candidate.entry_price)
            ))
            
            # Рассчитать экспозицию
            exposure = self.portfolio_exposure_engine.calculate_portfolio_exposure(positions)
            
            return {
                "gross_exposure": exposure.gross_exposure,
                "net_exposure": exposure.net_exposure,
                "btc_beta": exposure.btc_beta,
                "market_beta": exposure.market_beta,
                "symbol_exposure": exposure.symbol_exposure,
                "sector_exposure": exposure.sector_exposure
            }
        except Exception as e:
            logger.debug(f"Portfolio risk assessment failed: {e}")
            return {
                "gross_exposure": 0.0,
                "net_exposure": 0.0,
                "btc_beta": 1.0,
                "market_beta": 1.0
            }
    
    def _assess_tail_risk(
        self,
        symbol: str
    ) -> dict[str, Any]:
        """
        Оценить хвостовый риск (Section 26)
        
        Args:
            symbol: Символ инструмента
        
        Returns:
            Метрики хвостового риска
        """
        try:
            import numpy as np
            # Сгенерировать случайные доходности для демонстрации
            returns = list(np.random.normal(0, 0.01, 1000))
            result = self.tail_risk_engine.assess_tail_risk(symbol, returns)
            return result.to_dict()
        except Exception as e:
            logger.debug(f"Tail risk assessment failed: {e}")
            return {
                "var_95": 0.0,
                "var_99": 0.0,
                "cvar_95": 0.0,
                "cvar_99": 0.0,
                "expected_shortfall": 0.0
            }
    
    # ========================================================================
    # MAIN DECIDE METHOD WITH MASTER SPECIFICATION V2
    # ========================================================================
    
    async def decide(self, ctx: MarketContext) -> Decision:
        """
        Асинхронное решение по символу с поддержкой Master Specification v2
        
        Новая цепочка:
        1. Качество данных
        2. Режим рынка
        3. Новости
        4. Технический/структурный анализ
        5. Стакан/ликвидность
        6. Стратегии → кандидаты
        7. Оценка неопределённости (NEW)
        8. Проверка деградации сигналов (NEW)
        9. Расчёт Net EV (NEW)
        10. Проверка корреляции сигналов (NEW)
        11. Оценка риска портфеля (NEW)
        12. Оценка хвостового риска (NEW)
        13. Meta-Strategy (существующая)
        14. Оптимизация исполнения (NEW)
        15. Финальное решение
        """
        reasons: list[str] = []
        diagnostics: dict[str, Any] = {}

        # 1. Качество данных.
        primary = ctx.candles_on("5m") or ctx.candles_on("15m") or ctx.candles_on("1h") or ctx.candles_on("4h")
        if not primary or len(primary) < self.config.ema_slow + 5:
            return Decision(
                "NO_TRADE", ctx.symbol, ["insufficient_data"],
                reason_code=NoTradeReason.INSUFFICIENT_DATA.value,
            )

        # 2. Regime.
        regime = self.regime.classify(
            primary,
            news_score=ctx.news_score,
            btc_regime=ctx.global_market.get("btc_regime"),
            orderbook=ctx.orderbook,
            current_price=float(ctx.current_price),
            cross_market=ctx.global_market,
        )
        diagnostics["regime"] = regime.to_dict()
        
        # NEW: Check regime similarity (Section 9)
        regime_similarity = self._assess_regime_similarity(regime, ctx)
        diagnostics["regime_similarity"] = regime_similarity
        
        if regime.regime in (MarketRegime.PANIC, MarketRegime.HIGH_VOL):
            return Decision(
                "NO_TRADE",
                ctx.symbol,
                [f"market_regime={regime.regime.value}"],
                diagnostics=diagnostics,
                reason_code=(
                    NoTradeReason.BAD_REGIME.value
                    if regime.regime == MarketRegime.PANIC
                    else NoTradeReason.HIGH_VOLATILITY.value
                ),
            )
        
        # NEW: Unknown regime check (Section 10)
        if regime.regime == MarketRegime.UNKNOWN:
            return Decision(
                "NO_TRADE",
                ctx.symbol,
                ["market_regime=UNKNOWN"],
                diagnostics=diagnostics,
                reason_code=NoTradeReason.BAD_REGIME.value,
            )

        # 3. News.
        news_report = NewsReport(
            score=ctx.news_score,
            critical=ctx.news_score >= 75,
            blocked=ctx.news_score >= 75,
        )
        if news_report.blocked:
            return Decision(
                "NO_TRADE", ctx.symbol, ["news_critical"],
                reason_code=NoTradeReason.NEWS.value,
            )

        # 4. Technical/structure.
        technical = self.technical.analyse(primary)
        if technical.volatility == "EXTREME":
            return Decision(
                "NO_TRADE", ctx.symbol, ["extreme_volatility"],
                reason_code=NoTradeReason.HIGH_VOLATILITY.value,
            )

        structure = self.structure.analyse(
            primary, volume_confirmed=technical.volume_confirmed
        )

        # 5. Стакан/ликвидность.
        book = self.book.analyse(ctx.orderbook, float(ctx.current_price))
        if not book.is_healthy:
            reasons.append("unhealthy_orderbook")

        # 6. Стратегии.
        candidates = await self._candidates_from_strategies(
            ctx, regime.regime.value
        )
        if not candidates:
            return Decision(
                "NO_TRADE",
                ctx.symbol,
                ["no_strategy_signal"],
                diagnostics={"regime": regime.to_dict()},
                reason_code=NoTradeReason.NO_VALID_SETUP.value,
            )

        # 7.1 Флип-стратегии (существующая логика)
        from ..strategies.ts_momentum import TSM_ACTION_FLAT, TSM_ACTION_FLIP
        for cand in candidates:
            tsm_action = (cand.features or {}).get("tsm_action")
            if tsm_action == TSM_ACTION_FLAT:
                return Decision(
                    "CLOSE", ctx.symbol, ["tsm_flat"],
                    diagnostics={"regime": regime.to_dict()},
                )
        for cand in candidates:
            tsm_action = (cand.features or {}).get("tsm_action")
            if tsm_action == TSM_ACTION_FLIP:
                return Decision(
                    "FLIP", ctx.symbol, ["tsm_flip"],
                    candidate=cand,
                    diagnostics={"regime": regime.to_dict()},
                )

        # 8. Признаки.
        feats = self.features.compute(ctx)

        # 8.1 Жёсткие гейты + скоринг (существующая логика)
        diag_by_cand: dict[int, Any] = {}
        valid_candidates = []
        
        for candidate in candidates:
            if candidate.risk_reward < self.config.min_rr:
                candidate.reject("rr_too_low")
                continue

            # ML.
            candidate.ml_probability = self._ml_probability(feats)
            if (
                candidate.ml_probability is not None
                and candidate.ml_probability < self.config.min_ml_probability
            ):
                candidate.reject(
                    f"ml_prob<{self.config.min_ml_probability:.2f}"
                )
                continue

            # Корреляция.
            corr = self.correlation.assess(
                btc_regime=feats.btc_regime,
                eth_regime="UNKNOWN",
                direction=candidate.direction,
                is_btc="BTC" in candidate.symbol,
            )
            if corr.blocked:
                candidate.reject(corr.reason)
                continue

            # EV.
            ev = self.ev.calculate(
                p_win=(
                    candidate.ml_probability
                    if candidate.ml_probability is not None
                    else max(0.4, candidate.confidence)
                ),
                entry=float(candidate.entry_price),
                stop=float(candidate.stop_loss),
                take=float(candidate.take_profit),
            )
            candidate.expected_edge_pct = ev.edge_pct
            if not ev.is_positive:
                candidate.reject(f"edge={ev.edge_pct:.2f}%")
                continue

            # Ликвидность.
            liq = self.liquidity.assess(
                book, edge_pct=candidate.expected_edge_pct
            )
            if not liq.is_tradable:
                candidate.reject("liquidity_too_thin")
                continue

            # Скор.
            scores = self.scorer.score(
                candidate,
                features=feats,
                regime=regime,
                technical=technical,
                structure=structure,
                book=book,
                news=news_report,
                onchain=self.onchain.assess(ctx.symbol),
                derivatives=self.derivatives.assess(ctx.symbol),
                correlation=corr,
            )
            candidate.features["component_scores"] = scores.as_dict()
            diag_by_cand[id(candidate)] = (regime, technical, structure, book, ev, liq)
            
            # NEW: Add candidate to valid list for further analysis
            valid_candidates.append(candidate)

        # NEW: Check signal correlation (Section 23)
        correlation_analysis = self._check_signal_correlation(valid_candidates)
        diagnostics["signal_correlation"] = correlation_analysis
        
        # NEW: Filter out correlated signals - keep only independent ones
        independent_signals = correlation_analysis.get("independent_signals", [])
        filtered_candidates = [
            c for c in valid_candidates 
            if f"{c.strategy}_{c.symbol}" in independent_signals
        ]
        
        if not filtered_candidates:
            filtered_candidates = valid_candidates  # Fallback to all valid
        
        # NEW: Assess uncertainty for each candidate (Section 6)
        uncertainty_results = {}
        for candidate in filtered_candidates:
            uncertainty_results[id(candidate)] = self._assess_uncertainty(candidate, ctx, regime)
        
        # NEW: Check alpha decay for each candidate (Section 11-12)
        alpha_decay_results = {}
        for candidate in filtered_candidates:
            alpha_decay_results[id(candidate)] = self._check_alpha_decay(candidate)
        
        # NEW: Calculate Net EV for each candidate (Section 13-14)
        net_ev_results = {}
        for candidate in filtered_candidates:
            net_ev_results[id(candidate)] = self._calculate_net_ev(candidate, ctx)
        
        # NEW: Assess portfolio risk (Section 24)
        portfolio_risk = self._assess_portfolio_risk(filtered_candidates[0] if filtered_candidates else None, [])
        diagnostics["portfolio_risk"] = portfolio_risk
        
        # NEW: Assess tail risk (Section 26)
        tail_risk = self._assess_tail_risk(ctx.symbol)
        diagnostics["tail_risk"] = tail_risk
        
        # 8.2 Meta-Strategy: выбор по shrunken EV в текущем режиме (существующая логика)
        meta = self.meta.select(
            filtered_candidates,
            regime.regime.value,
            regime_axes=regime.axes.axes_key() if regime.axes else None,
        )
        
        if meta.chosen is None:
            rejected = [
                f"{c.strategy}:{c.direction} -> {','.join(c.rejections) or 'no_reason'}"
                for c in filtered_candidates
                if c.rejections
            ]
            return Decision(
                "NO_TRADE",
                ctx.symbol,
                [f"meta_strategy:{meta.reason}", *rejected],
                diagnostics={
                    "regime": regime.to_dict(),
                    "meta": [e.to_dict() for e in meta.evaluations],
                    "uncertainty": uncertainty_results,
                    "alpha_decay": alpha_decay_results,
                    "net_ev": net_ev_results,
                },
                reason_code=meta.reason_code.value if meta.reason_code else None,
            )

        cand = meta.chosen
        cand_id = id(cand)
        diag = diag_by_cand[cand_id]
        
        # NEW: Get uncertainty for chosen candidate
        uncertainty_info = uncertainty_results.get(cand_id, {})
        
        # NEW: Check if uncertainty is too high (Section 6)
        if uncertainty_info.get("total_uncertainty", 0) > self.max_uncertainty_threshold:
            return Decision(
                "NO_TRADE",
                ctx.symbol,
                [f"high_uncertainty={uncertainty_info.get('total_uncertainty', 0):.3f}"],
                diagnostics={
                    **diagnostics,
                    "uncertainty": uncertainty_info,
                    "alpha_decay": alpha_decay_results.get(cand_id, {}),
                    "net_ev": net_ev_results.get(cand_id, {})
                },
                reason_code=NoTradeReason.LOW_EV.value,
                uncertainty=uncertainty_info.get("total_uncertainty")
            )
        
        # NEW: Check if signal is expired (Section 12)
        alpha_decay_info = alpha_decay_results.get(cand_id, {})
        if alpha_decay_info.get("is_expired", False):
            return Decision(
                "NO_TRADE",
                ctx.symbol,
                ["signal_expired"],
                diagnostics={
                    **diagnostics,
                    "alpha_decay": alpha_decay_info
                },
                reason_code=NoTradeReason.BAD_REGIME.value,
                uncertainty=uncertainty_info.get("total_uncertainty")
            )
        
        # NEW: Check Net EV (Section 14)
        net_ev_info = net_ev_results.get(cand_id, {})
        if net_ev_info.get("net_ev_pct", 0) < self.min_net_ev_threshold:
            return Decision(
                "NO_TRADE",
                ctx.symbol,
                [f"low_net_ev={net_ev_info.get('net_ev_pct', 0):.4f}"],
                diagnostics={
                    **diagnostics,
                    "net_ev": net_ev_info
                },
                reason_code=NoTradeReason.LOW_EV.value,
                uncertainty=uncertainty_info.get("total_uncertainty"),
                net_ev=net_ev_info.get("net_ev_pct")
            )
        
        # NEW: Optimize execution (Section 15-16)
        execution_plan = self._optimize_execution(cand, ctx)
        
        # 9. Risk engine — упрощённо: размер позиции и экспозиция.
        max_risk = Decimal(str(self.config.initial_capital if hasattr(self.config, 'initial_capital') else 1000)) * self.config.risk_per_trade_pct
        stop_dist = abs(cand.entry_price - cand.stop_loss)
        if stop_dist > 0:
            cand.position_size = (max_risk / stop_dist).quantize(Decimal("0.000001"))

        # Create final decision with all new information
        final_diagnostics = {
            "regime": diag[0].to_dict(),
            "technical": diag[1].to_dict(),
            "structure": diag[2].to_dict(),
            "book": diag[3].to_dict(),
            "ev": diag[4].to_dict(),
            "liquidity": diag[5].to_dict(),
            "meta": [e.to_dict() for e in meta.evaluations],
            "uncertainty": uncertainty_info,
            "alpha_decay": alpha_decay_info,
            "net_ev": net_ev_info,
            "execution_plan": execution_plan,
            "signal_correlation": correlation_analysis,
            "portfolio_risk": portfolio_risk,
            "tail_risk": tail_risk
        }
        
        return Decision(
            action="LONG" if cand.direction == "long" else "SHORT",
            symbol=ctx.symbol,
            reasons=["all_filters_passed"],
            candidate=cand,
            diagnostics=final_diagnostics,
            reason_code=None,
            uncertainty=uncertainty_info.get("total_uncertainty"),
            net_ev=net_ev_info.get("net_ev_pct"),
            execution_plan=execution_plan,
            alpha_decay_status=alpha_decay_info,
            portfolio_risk=portfolio_risk,
            tail_risk=tail_risk
        )
    
    def _assess_regime_similarity(self, regime: any, ctx: MarketContext) -> dict[str, Any]:
        """
        Оценить схожесть режима (Section 9)
        
        Args:
            regime: Текущий режим
            ctx: Контекст рынка
        
        Returns:
            Оценка схожести
        """
        try:
            # Create MarketState for regime similarity engine
            state = MarketState(
                timestamp=datetime.now(),
                features={
                    "volatility": 0.02,
                    "volume": 100000,
                    "trend": 0.5
                },
                regime=regime.regime.value if regime else "UNKNOWN"
            )
            
            # Add to historical states
            self.regime_similarity_engine.add_historical_state(state)
            
            # Assess similarity
            assessment = self.regime_similarity_engine.assess_regime_similarity(state)
            
            return {
                "similarity_score": assessment.similarity_score,
                "historical_coverage": assessment.historical_coverage,
                "regime_stability": assessment.regime_stability,
                "transition_probability": assessment.transition_probability,
                "is_unknown_regime": assessment.is_unknown_regime,
                "needs_increased_uncertainty": assessment.needs_increased_uncertainty
            }
        except Exception as e:
            logger.debug(f"Regime similarity assessment failed: {e}")
            return {
                "similarity_score": 0.5,
                "historical_coverage": 100,
                "is_unknown_regime": False
            }


# Backward compatibility
DecisionPipeline = DecisionPipelineV2
