from __future__ import annotations

from astra_bot.decision import candidate_filters, htf_gates
from astra_bot.decision.shared_market_data import bind_shared_bingx_client
from astra_bot.strategies import funding_rate_contrarian, open_interest_divergence


def test_auxiliary_market_consumers_share_runner_client() -> None:
    client = object()
    bind_shared_bingx_client(client)  # type: ignore[arg-type]
    try:
        assert candidate_filters._lazy_bingx_client is client
        assert htf_gates._lazy_bingx_client is client
        assert funding_rate_contrarian._lazy_bingx_client is client
        assert open_interest_divergence._lazy_bingx_client is client
    finally:
        bind_shared_bingx_client(None)
