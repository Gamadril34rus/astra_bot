"""Force paper/demo pipeline to use ONLY pattern_4h_retest.

Imported early so the strategy zoo cannot trade or pollute DEMO stats.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def apply() -> None:
    try:
        from astra_bot.decision.pipeline import DecisionPipeline
        from astra_bot.strategies.pattern_4h_retest import Pattern4hRetestStrategy
    except Exception as exc:
        logger.error("force_pattern_only import failed: %s", exc)
        return

    if getattr(DecisionPipeline, "_pattern_only_patched", False):
        return

    _orig_init = DecisionPipeline.__init__

    def _init(self, config=None, strategies=None, model=None, stats_store=None):
        only = [Pattern4hRetestStrategy()]
        _orig_init(
            self, config=config, strategies=only, model=model, stats_store=stats_store
        )
        names = [getattr(s, "name", type(s).__name__) for s in self.strategies]
        logger.info("force_pattern_only: pipeline locked to %s", names)

    DecisionPipeline.__init__ = _init  # type: ignore[method-assign]
    DecisionPipeline._pattern_only_patched = True  # type: ignore[attr-defined]
    logger.info("force_pattern_only: DecisionPipeline patched")
