"""Bind market-data helpers to the runner-owned BingX client."""

from __future__ import annotations

from ..adapters.bingx.client import BingXClient


def bind_shared_bingx_client(client: BingXClient | None) -> None:
    """Share one session/rate limiter across auxiliary strategy data consumers."""
    from . import candidate_filters, htf_gates
    from ..strategies import funding_rate_contrarian, open_interest_divergence

    candidate_filters.set_bingx_client(client)
    htf_gates.set_bingx_client(client)
    funding_rate_contrarian.set_bingx_client(client)
    open_interest_divergence.set_bingx_client(client)
