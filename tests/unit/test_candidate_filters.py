from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from astra_bot.decision.candidate_filters import btc_dominance_filter
from astra_bot.decision.context import SignalCandidate


@pytest.mark.asyncio
async def test_apply_candidate_filters_graceful():
    cand = SignalCandidate(
        symbol="ETH-USDT",
        direction="long",
        entry_price=Decimal("2000"),
        stop_loss=Decimal("1950"),
        take_profit=Decimal("2100"),
        timeframe="1h",
        confidence=0.7,
        strategy="test_strategy",
    )

    with patch("astra_bot.decision.candidate_filters._get_btc_dominance", new_callable=AsyncMock) as mock_btcd:
        mock_btcd.return_value = (55.0, 0.8)  # BTC.D вырос > 0.5% -> Confidence альта должен снизиться на -0.10
        res = await btc_dominance_filter([cand])
        assert len(res) == 1
        assert pytest.approx(res[0].confidence, 0.01) == 0.60
