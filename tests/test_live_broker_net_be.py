from decimal import Decimal
from types import SimpleNamespace
from astra_bot.decision.live_broker import LiveBroker, LiveBrokerConfig

def test_live_net_breakeven_differs_from_entry():
    broker = LiveBroker(client=SimpleNamespace(), config=LiveBrokerConfig())
    pos = SimpleNamespace(
        direction="long",
        entry_price=Decimal("100"),
        fill_price=Decimal("100"),
        quantity=Decimal("1"),
        funding_accum=Decimal("0"),
    )
    be = broker.net_breakeven_price(pos)
    assert be != Decimal("100")
    assert be > Decimal("100")  # long needs higher exit to cover fees
    pos_s = SimpleNamespace(
        direction="short", entry_price=Decimal("100"), fill_price=Decimal("100"),
        quantity=Decimal("1"), funding_accum=Decimal("0"),
    )
    be_s = broker.net_breakeven_price(pos_s)
    assert be_s < Decimal("100")

def test_net_be_with_funding():
    broker = LiveBroker(client=SimpleNamespace(), config=LiveBrokerConfig())
    pos = SimpleNamespace(
        direction="long", entry_price=Decimal("100"), fill_price=Decimal("100"),
        quantity=Decimal("10"), funding_accum=Decimal("1.0"),  # paid 1 USDT
    )
    be = broker.net_breakeven_price(pos)
    # fee component + 0.1 per unit funding
    assert be > Decimal("100.1")
