
import pytest
from astra_bot.strategies.fair_value_gap import FairValueGapStrategy
from astra_bot.strategies.funding_rate_contrarian import FundingRateContrarianStrategy
from astra_bot.strategies.liquidity_sweep import LiquiditySweepStrategy
from astra_bot.strategies.open_interest_divergence import OpenInterestDivergenceStrategy
from astra_bot.strategies.order_block import OrderBlockStrategy
from astra_bot.strategies.range_breakout_retest import RangeBreakoutRetestStrategy
from astra_bot.strategies.ts_momentum_cross import TSMomentumCrossStrategy
from astra_bot.strategies.volatility_breakout import VolatilityBreakoutStrategy
from astra_bot.strategies.volume_delta import VolumeDeltaStrategy
from astra_bot.strategies.vwap_deviation import VWAPDeviationStrategy
from astra_bot.strategies.zscore_mean_reversion import ZScoreMeanReversionStrategy

STRATEGIES = [
    LiquiditySweepStrategy,
    FairValueGapStrategy,
    OrderBlockStrategy,
    RangeBreakoutRetestStrategy,
    ZScoreMeanReversionStrategy,
    VolatilityBreakoutStrategy,
    TSMomentumCrossStrategy,
    FundingRateContrarianStrategy,
    VolumeDeltaStrategy,
    VWAPDeviationStrategy,
    OpenInterestDivergenceStrategy,
]


@pytest.mark.asyncio
@pytest.mark.parametrize("StratClass", STRATEGIES)
async def test_new_strategies_disabled_guard(StratClass):
    strat = StratClass()
    strat.config.enabled = False

    # Pass dummy inputs
    res = await strat.evaluate("BTC-USDT", candles=[])

    assert res is None
