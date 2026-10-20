
from astra_bot.decision.live_funnel import empty_skipped, bump, identity_ok, arm_summary_row, unattributed

def test_identity_and_decomp():
    sk = empty_skipped()
    bump(sk, "margin_M2")
    bump(sk, "retest_not_triggered")
    assert sk["margin_M2"] == 1
    assert identity_ok(3, [{"a": 1}], sk)
    assert unattributed(sk) == 0
    bump(sk, "other")
    assert unattributed(sk) == 1
    row = arm_summary_row(candidates=4, entered=[{"s":1}], skipped=sk, unrealized_sum=1.0,
                          dry=False, entries_allowed=True, halt=None, ts=1.0)
    assert row["identity_ok"] is True

def test_unknown_reason_goes_other():
    sk = empty_skipped()
    bump(sk, "weird_new")
    assert sk["other"] == 1

def test_equity_unknown_and_sized_qty_zero_in_identity():
    sk = empty_skipped()
    bump(sk, "equity_unknown")
    bump(sk, "sized_qty_zero")
    assert "equity_unknown" in sk and sk["equity_unknown"] == 1
    assert sk["sized_qty_zero"] == 1
    # 2 skipped, 0 entered → candidates 2
    assert identity_ok(2, [], sk) is True
    assert identity_ok(3, [{"x": 1}], sk) is True
    assert identity_ok(5, [], sk) is False
