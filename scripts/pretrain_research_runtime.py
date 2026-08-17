#!/usr/bin/env python3
"""Run the research-first pretrain with a bounded feature window.

The historical dataset can contain hundreds of thousands of candles. Most ASTRA
features only need the recent ~200 bars, so passing the complete history into
the feature engine at every observation turns an otherwise linear scan into an
accidental O(n^2) workload. This wrapper keeps the research semantics intact
while bounding feature calculation to a recent window.
"""
from __future__ import annotations

import asyncio

from astra_bot.ml import research_engine
from astra_bot.ml.market_understanding import compute_market_features as _compute_market_features

_FEATURE_WINDOW = 260


def _bounded_features(candles, timeframe="1h", extra_features=None):
    return _compute_market_features(
        candles[-_FEATURE_WINDOW:],
        timeframe=timeframe,
        extra_features=extra_features,
    )


# research_history_v2 resolves compute_market_features from its module globals.
research_engine.compute_market_features = _bounded_features

from scripts.pretrain_5y_enhanced import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
