"""Decision pipeline — единая точка входа.

Data → Regime → Strategy → ML → EV → Risk → Execution.

Любой компонент может вернуть NO_TRADE. Финальное решение всегда
в ``Decision`` с явным списком причин отказа.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .config import DecisionConfig
from .context import MarketContext, SignalCandidate
import asyncio
import inspect
from .correlation_engine import CorrelationEngine
from .derivatives_engine import DerivativesEngine
from .ev_engine import EVEngine
from .feature_engine import FeatureEngine
from .liquidity_engine import LiquidityEngine
from .news_engine import NewsEngine, NewsReport
from .onchain_engine import OnChainEngine
from .orderbook_engine import OrderBookEngine
from .regime_engine import MarketRegime, RegimeEngine
from .scoring import SignalScorer
from .structure_engine import StructureEngine
from .technical_engine import TechnicalEngine

logger = logging.getLogger(__name__)


@dataclass
class Decision:
    action: str  # LONG / SHORT / NO_TRADE / FLIP / CLOSE
    symbol: str
    reasons: list[str] = field(default_factory=list)
    candidate: SignalCandidate | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "symbol": self.symbol,
            "reasons": self.reasons,
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "diagnostics": self.diagnostics,
        }


class DecisionPipeline:
    def __init__(
        self,
        config: DecisionConfig | None = None,
        strategies: list | None = None,
        model: Any | None = None,
    ):
        self.config = config or DecisionConfig()
        self.strategies = strategies or []
        self.model = model

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

    # ----------------------------------------------------------- builders
    def _run_evaluate(self, strategy, **kwargs):
        """Запустить evaluate стратегии, корректно дождавшись корутины.

        Раньше тут был ``asyncio.run`` внутри синхронного ``decide``, что
        падало с "asyncio.run cannot be called from a running event loop"
        в живом движке — поэтому сигналы не генерировались.
        """
        maybe = strategy.evaluate(**kwargs)
        if not inspect.iscoroutine(maybe):
            return maybe
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(maybe)
        # Уже внутри event loop: гоняем корутину до завершения в отдельном
        # потоке со своим циклом, чтобы не ломать вызывающий loop.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(asyncio.run, maybe).result()

    def _candidates_from_strategies(
        self,
        ctx: MarketContext,
        regime: str,
    ) -> list[SignalCandidate]:
        out: list[SignalCandidate] = []
        primary = ctx.candles_on("5m") or ctx.candles_on("15m") or ctx.candles_on("1h") or ctx.candles_on("4h") or []
        if not primary:
            return out
        for strategy in self.strategies:
            try:
                # Стратегия может запросить конкретный таймфрейм
                # (например, ts_momentum оценивается на 4h-свечах).
                preferred_tf = getattr(strategy, "preferred_timeframe", None)
                if preferred_tf and ctx.candles_on(preferred_tf):
                    candles = ctx.candles_on(preferred_tf)
                else:
                    candles = primary
                signal = self._run_evaluate(
                    strategy,
                    symbol=ctx.symbol,
                    candles=candles,
                    orderbook=ctx.orderbook,
                    current_price=float(ctx.current_price),
                    market_regime=regime,
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
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("strategy %s failed: %s", strategy, exc)
        return out

    def _ml_probability(self, feats) -> float | None:
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

    # ----------------------------------------------------------- main
    def decide(self, ctx: MarketContext) -> Decision:
        reasons: list[str] = []

        # 1. Качество данных.
        primary = ctx.candles_on("5m") or ctx.candles_on("15m") or ctx.candles_on("1h") or ctx.candles_on("4h")
        if not primary or len(primary) < self.config.ema_slow + 5:
            return Decision("NO_TRADE", ctx.symbol, ["insufficient_data"])

        # 2. Regime.
        regime = self.regime.classify(
            primary,
            news_score=ctx.news_score,
            btc_regime=ctx.global_market.get("btc_regime"),
        )
        if regime.regime in (MarketRegime.PANIC, MarketRegime.HIGH_VOL):
            return Decision(
                "NO_TRADE",
                ctx.symbol,
                [f"market_regime={regime.regime.value}"],
                diagnostics={"regime": regime.to_dict()},
            )

        # 3. News.
        news_report = NewsReport(
            score=ctx.news_score,
            critical=ctx.news_score >= 75,
            blocked=ctx.news_score >= 75,
        )
        if news_report.blocked:
            return Decision("NO_TRADE", ctx.symbol, ["news_critical"])

        # 4. Technical/structure.
        technical = self.technical.analyse(primary)
        if technical.volatility == "EXTREME":
            return Decision("NO_TRADE", ctx.symbol, ["extreme_volatility"])

        structure = self.structure.analyse(
            primary, volume_confirmed=technical.volume_confirmed
        )

        # 5. Стакан/ликвидность.
        book = self.book.analyse(ctx.orderbook, float(ctx.current_price))
        if not book.is_healthy:
            reasons.append("unhealthy_orderbook")

        # 6. Корреляция с BTC. Значение используется в цикле кандидатов.

        # 7. Стратегии.
        candidates = self._candidates_from_strategies(ctx, regime.regime.value)
        if not candidates:
            return Decision(
                "NO_TRADE",
                ctx.symbol,
                ["no_strategy_signal"],
                diagnostics={"regime": regime.to_dict()},
            )

        # 7.1 Флип-стратегии (ts_momentum): смена режима — детерминированное
        # действие, которое не должно зависеть от скоринга конкурентов.
        # CLOSE — выйти из рынка, FLIP — перевернуть позицию.
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

        best: tuple[SignalCandidate, Any] | None = None
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

            if best is None or candidate.total_score > best[0].total_score:
                best = (candidate, (regime, technical, structure, book, ev, liq))

        if best is None:
            rejected = [
                f"{c.strategy}:{c.direction} -> {','.join(c.rejections) or 'no_reason'}"
                for c in candidates
            ]
            return Decision(
                "NO_TRADE", ctx.symbol, rejected,
                diagnostics={"regime": regime.to_dict()},
            )

        cand, diag = best
        # 9. Risk engine — упрощённо: размер позиции и экспозиция.
        max_risk = Decimal(str(self.config.initial_capital if hasattr(self.config, 'initial_capital') else 1000)) * self.config.risk_per_trade_pct
        stop_dist = abs(cand.entry_price - cand.stop_loss)
        if stop_dist > 0:
            cand.position_size = (max_risk / stop_dist).quantize(Decimal("0.000001"))

        return Decision(
            action="LONG" if cand.direction == "long" else "SHORT",
            symbol=ctx.symbol,
            reasons=["all_filters_passed"],
            candidate=cand,
            diagnostics={
                "regime": diag[0].to_dict(),
                "technical": diag[1].to_dict(),
                "structure": diag[2].to_dict(),
                "book": diag[3].to_dict(),
                "ev": diag[4].to_dict(),
                "liquidity": diag[5].to_dict(),
            },
        )
