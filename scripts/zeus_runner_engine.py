"""Zeus paper-clock split 6/9: pipeline+engine build; FIX: stats_store on ZEUS path."""

from __future__ import annotations

import argparse

from zeus_runner_common import (
    ZEUS_STATS_PATH,
    BingXClient,
    DecisionConfig,
    DecisionPipeline,
    Path,
    TradingEngine,
    TradingEngineConfig,
    ZeusChannelBoundaryStrategy,
    ZeusWedgeRetestStrategy,
    logger,
    os,
)

CONF_RISK_LADDER = ((0.88, 3.0), (0.80, 2.0), (0.70, 1.5))  # conf -> risk %
ZEUS_HEAT_PATH = "models/zeus_heat_state.json"


def zeus_build_pipeline_and_engine(
    args: argparse.Namespace,
    symbols: tuple[str, ...],
    config: TradingEngineConfig,
    bingx: BingXClient,
) -> tuple[TradingEngine, ZeusWedgeRetestStrategy, ZeusChannelBoundaryStrategy]:
    # Capital path: channel TP2 density + matched wedge quality.
    # Leverage ladder: confidence→2..20x (env ASTRA_LEVERAGE_MAX);
    # actual risk $ scales 1–3% via CONF_RISK_LADDER on conf (sizing + notes).
    # Heat governor: 3 consecutive losses → risk capped at 1% until UTC day end or win.
    import sys as _sys
    from pathlib import Path as _P

    _scripts = str(_P(__file__).resolve().parent)
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    from zeus_research_wrap import (
        ZeusChannelCapitalStrategy,
        ZeusMatchedResearchStrategy,
        channel_capital_config,
        research_config,
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
    config.conf_risk_ladder = CONF_RISK_LADDER
    config.max_open_positions = 3
    engine = TradingEngine(
        exchange=bingx, pipeline=pipeline, config=config, notifier=None
    )
    # Day/week loss limits (Zeus-only): 4% / 6%. HALT / 2-stop / kill-switch untouched.
    try:
        engine.risk.config.daily_loss_limit = _DRisk("0.04")
        engine.risk.config.weekly_loss_limit = _DRisk("0.06")
        logger.info(
            "Zeus risk limits applied: day=%.1f%% week=%.1f%% (conf ladder 1-3%%)",
            float(engine.risk.config.daily_loss_limit) * 100,
            float(engine.risk.config.weekly_loss_limit) * 100,
        )
    except Exception as _lim_exc:
        logger.warning("risk limit override FAILED: %s", _lim_exc)

    # Heat governor (persistent). Caps ladder to 1% after 3 consecutive losses.
    from astra_bot.decision.zeus_heat import ZeusHeatGovernor

    heat = ZeusHeatGovernor(state_path=Path(ZEUS_HEAT_PATH))
    engine._zeus_heat = heat  # type: ignore[attr-defined]
    if heat.is_heat_active():
        logger.warning(
            "Zeus heat ACTIVE at start (consecutive=%s day=%s) — risk capped at 1%%",
            heat.consecutive,
            heat.heat_day,
        )

    # Actual sizing budget + notes risk_budget both use conf ladder (or heat 1%).
    # Kelly / clamps / slots / LEVERAGE_LADDER / RiskEngine check_trade untouched.
    try:
        import types as _types

        from astra_bot.decision.conf_risk_ladder import conf_risk_pct

        _orig_size = engine._position_size
        _orig_open = engine.broker.open_position
        _orig_log = engine.broker._log_trade

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
                if heat.is_heat_active():
                    self.config.risk_per_trade_pct = _DRisk("0.01")
                else:
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

        def _open_with_ladder(self, *args, **kwargs):
            notes = dict(kwargs.get("notes") or {})
            conf = notes.get("confidence")
            eq = notes.get("equity_before")
            if eq is None:
                try:
                    eq = float(self.net_equity)
                except Exception:
                    eq = None
            heat_on = heat.is_heat_active()
            if eq is not None:
                if heat_on:
                    tier = _DRisk("0.01")
                    notes["binding_constraint"] = "heat_cap"
                else:
                    tier = conf_risk_pct(
                        float(conf) if conf is not None else None,
                        CONF_RISK_LADDER,
                        _DRisk("0.01"),
                    )
                notes["risk_budget"] = float(_DRisk(str(eq)) * tier)
                kwargs["notes"] = notes
            elif heat_on:
                notes["binding_constraint"] = "heat_cap"
                kwargs["notes"] = notes
            return _orig_open(*args, **kwargs)

        def _log_with_heat(self, trade):
            try:
                heat.record(float(getattr(trade, "pnl", 0) or 0))
            except Exception as _h_exc:
                logger.debug("zeus heat record: %s", _h_exc)
            return _orig_log(trade)

        engine._position_size = _types.MethodType(_position_size, engine)
        engine.broker.open_position = _types.MethodType(
            _open_with_ladder, engine.broker
        )
        engine.broker._log_trade = _types.MethodType(_log_with_heat, engine.broker)
    except Exception as _sz_exc:
        logger.warning("conf risk / heat wrap FAILED: %s", _sz_exc)
    logger.info(
        "Zeus paper leverage_max=%s (conf risk 1-3%%; heat@%s losses; day 4%% week 6%%)",
        _lev_max,
        heat.threshold,
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
