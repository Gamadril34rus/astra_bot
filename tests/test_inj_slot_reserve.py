"""INJ slot reserve: open_n <= MAX_OPEN - 2 (AUDIT 800/801)."""
from __future__ import annotations

from astra_bot.decision.live_arm import (
    MAX_OPEN,
    INJ_SLOT_RESERVE,
    inj_slot_blocks,
)


def test_reserve_constant():
    assert MAX_OPEN == 5
    assert INJ_SLOT_RESERVE == 2


def test_inj_allowed_when_open_le_3():
    for n in (0, 1, 2, 3):
        assert inj_slot_blocks("INJ-USDT", n) is False


def test_inj_blocked_when_open_ge_4():
    for n in (4, 5):
        assert inj_slot_blocks("INJ-USDT", n) is True


def test_scenario_four_open_inj_blocked_slot_remains():
    """4 positions open → INJ blocked; major may still use remaining slot."""
    open_n = 4
    assert open_n < MAX_OPEN  # one slot left
    assert inj_slot_blocks("INJ-USDT", open_n) is True
    assert inj_slot_blocks("BTC-USDT", open_n) is False


def test_scenario_three_open_inj_enters():
    """3 open → INJ allowed on equal footing."""
    assert inj_slot_blocks("INJ-USDT", 3) is False


def test_majors_never_blocked_by_inj_rule():
    for sym in ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT"):
        assert inj_slot_blocks(sym, 4) is False
        assert inj_slot_blocks(sym, 5) is False
