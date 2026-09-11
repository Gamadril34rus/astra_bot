"""Cost model extra fields do not change fill math (TZ P1.9)."""

from __future__ import annotations

from decimal import Decimal

from astra_bot.engines.cost_model import CostModel


def test_exchange_constraint_fields_are_inert():
    base = CostModel(taker_fee_rate=Decimal("0.001"), slippage_pct=Decimal("0.001"))
    extra = CostModel(
        taker_fee_rate=Decimal("0.001"),
        slippage_pct=Decimal("0.001"),
        min_notional=Decimal("5"),
        tick_size=Decimal("0.1"),
        lot_size=Decimal("0.001"),
        reject_penalty=Decimal("1"),
        rate_limit_penalty=Decimal("1"),
    )
    price, qty = Decimal("100"), Decimal("1")
    assert extra.effective_entry_price(price, "long") == base.effective_entry_price(
        price, "long"
    )
    assert extra.entry_fee(price, qty, "long") == base.entry_fee(price, qty, "long")
    assert extra.min_notional == Decimal("5")
