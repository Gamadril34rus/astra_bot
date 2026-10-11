"""Unique BingX clientOrderId tags (no fixed arm-e)."""
from astra_bot.decision.live_broker import unique_client_order_id


def test_two_orders_different_tags():
    a = unique_client_order_id("BTC-USDT", "e")
    b = unique_client_order_id("BTC-USDT", "e")
    assert a != b
    assert a.startswith("arm-") and a.endswith("-e")
    assert "BTC" in a


def test_entry_stop_tp_roles_differ():
    e = unique_client_order_id("TIA-USDT", "e")
    s = unique_client_order_id("TIA-USDT", "s")
    t = unique_client_order_id("TIA-USDT", "t")
    assert len({e, s, t}) == 3
    assert e.endswith("-e") and s.endswith("-s") and t.endswith("-t")
    assert "TIA" in e
