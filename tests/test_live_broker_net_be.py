
from decimal import Decimal
from types import SimpleNamespace
from astra_bot.decision.live_broker import LiveBroker, LiveBrokerConfig
from astra_bot.engines.cost_model import BINGX_PERPS_TAKER_FEE

def test_live_net_breakeven_differs_from_entry():
    broker = LiveBroker(client=SimpleNamespace(), config=LiveBrokerConfig())
    pos = SimpleNamespace(
        direction="long", entry_price=Decimal("100"), fill_price=Decimal("100"),
        quantity=Decimal("1"), funding_accum=Decimal("0"),
    )
    be = broker.net_breakeven_price(pos)
    expected = Decimal("100") * (Decimal("1") + BINGX_PERPS_TAKER_FEE * 2)
    assert be == expected
    assert be != Decimal("100")
    pos_s = SimpleNamespace(
        direction="short", entry_price=Decimal("100"), fill_price=Decimal("100"),
        quantity=Decimal("1"), funding_accum=Decimal("0"),
    )
    assert broker.net_breakeven_price(pos_s) < Decimal("100")

def test_fee_from_config_not_hardcode():
    src = open("astra_bot/decision/live_broker.py").read()
    assert "BINGX_PERPS_TAKER_FEE" in src
    assert "0.0005" not in src.split("def net_breakeven_price")[1].split("async def reduce")[0]
