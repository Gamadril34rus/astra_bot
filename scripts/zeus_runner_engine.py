"""Zeus paper-clock split 6/9: pipeline+engine build; FIX: stats_store on ZEUS path."""

from __future__ import annotations

import argparse
import types

from zeus_runner_common import (
    BingXClient,
    DecisionConfig,
    DecisionPipeline,
    Path,
    TradingEngine,
    TradingEngineConfig,
    ZEUS_STATS_PATH,
    ZeusChannelBoundaryStrategy,
    ZeusWedgeRetestStrategy,
    logger,
    os,
)

CONF_RISK_LADDER = ((0.88, 3.0), (0.80, 2.0), (0.70, 1.5))  # conf -> risk %


def zeus_build_pipeline_and_engine(
    args: argparse.Namespace,
    symbols: tuple[str, ...],
    config: TradingEngineConfig,
    bingx: BingXClient,
) -> tuple[TradingEngine, ZeusWedgeRetestStrategy, ZeusChannelBoundaryStrategy]:
    # Capital path: channel TP2 density + matched wedge quality.
    # Leverage ladder: confidence→2..20x (env ASTRA_LEVERAGE_MAX);
    # risk $ scales 1–3% via CONF_RISK_LADDER on conf.
    import sys as _sys
    from pathlib import Path as _P

    _scripts = str(_P(__file__).resolve().parent)
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    from zeus_research_wrap import (
        ZeusMatchedResearchStrategy,
        research_config,
        ZeusChannelCapitalStrategy,
        channel_capital_config,
    )

    zeus = ZeusMatchedResearchStrategy(research_config())
    zeus_channel = ZeusChannelCapitalStrategy(channel_capital_config())
    dcfg = DecisionConfig()
    dcfg.min_rr = 2.0
    dcfg.min_ml_probability = 0.0
    dcfg.min_expected_edge_pct = 0.0
    dcfg.min_ev_r = 0.0
    from astra_bot.decision.strategy_stats import StrategyStatsStore

    zeus_stats = StrategyStatsStore(
        path=Path(ZEUS_STATS_PATH),
        shrinkage_k=dcfg.ev_shrinkage_k,
        min_samples=dcfg.min_ev_samples,
    )
    pipeline = DecisionPipeline(
        config=dcfg,
        strategies=[zeus_channel, zeus],
        model=None,
        stats_store=zeus_stats,
    )
    _lev_max = int(os.environ.get("ASTRA_LEVERAGE_MAX", "20") or "20")
    _lev_max = max(1, min(_lev_max, 50))
    config.leverage_max = _lev_max
    config.leverage_min_ev_r = 1.0
    from decimal import Decimal as _DRisk

    config.risk_per_trade_pct = _DRisk("0.01")
    # Optional attrs (TradingEngineConfig may not declare them yet).
    config.conf_risk_ladder = CONF_RISK_LADDER
    config.daily_loss_limit_pct = _DRisk("0.04")
    config.weekly_loss_limit_pct = _DRisk("0.06")
    config.max_open_positions = 3
    engine = TradingEngine(
        exchange=bingx, pipeline=pipeline, config=config, notifier=None
    )
    # Day/week loss limits (RiskEngine built with paper defaults 3%/6%).
    try:
        engine.risk.config.daily_loss_limit = _DRisk("0.04")
        engine.risk.config.weekly_loss_limit = _DRisk("0.06")
    except Exception as _lim_exc:
        logger.debug("risk limit override: %s", _lim_exc)
    # Conf → risk% on position sizing (temporarily raises risk_per_trade_pct).
    try:
        from astra_bot.decision.conf_risk_ladder import conf_risk_pct

        _orig_size = engine._position_size

        def _position_size(
            self,
            equity,
            entry,
            stop,
            ml_confidence=None,
            atr_pct=None,
            strategy="",
            timeframe="",
        ):
            saved = self.config.risk_per_trade_pct
            try:
                self.config.risk_per_trade_pct = conf_risk_pct(
                    ml_confidence, CONF_RISK_LADDER, _DRisk(str(saved))
                )
                return _orig_size(
                    equity,
                    entry,
                    stop,
                    ml_confidence=ml_confidence,
                    atr_pct=atr_pct,
                    strategy=strategy,
                    timeframe=timeframe,
                )
            finally:
                self.config.risk_per_trade_pct = saved

        engine._position_size = types.MethodType(_position_size, engine)
    except Exception as _sz_exc:
        logger.debug("conf risk ladder wrap: %s", _sz_exc)
    logger.info(
        "Zeus paper leverage_max=%s (conf risk ladder 1-3%%; day 4%% week 6%%)",
        _lev_max,
    )
    from decimal import Decimal as _Dec

    _cap = _Dec(str(args.capital))
    if _cap <= 0:
        _cap = _Dec("2000")
    try:
        br = engine.broker
        old_cap = getattr(br, "initial_capital", None)
        br.initial_capital = _cap
        if hasattr(engine, "risk") and hasattr(engine.risk, "set_capital"):
            engine.risk.set_capital(_cap, _cap)
        engine._capital_synced = True
        try:
            br.save()
        except Exception:
            pass
        logger.info("Zeus capital fixed to %s (was %s)", _cap, old_cap)
    except Exception as _cap_exc:
        logger.warning("capital fix skipped: %s", _cap_exc)
    return engine, zeus, zeus_channel
