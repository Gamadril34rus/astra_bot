"""Regression tests for persisted TP1 sizing and exit-card attribution."""

from decimal import Decimal

import pytest

from astra_bot.decision.live_broker import _estimate_tp1_leg, _tp_frac_from_plan
from astra_bot.decision.live_positions import get_plan, upsert_plan


def test_upsert_plan_persists_tp1_fraction(tmp_path):
    path = tmp_path / "positions.json"

    upsert_plan(
        "BTC-USDT",
        side="long",
        qty="2",
        entry="100",
        stop="98",
        tp="103",
        tp1="103",
        tp_frac="0.3",
        path=path,
    )

    plan = get_plan("BTC-USDT", path, side="long")

    assert plan is not None
    assert plan["tp_frac"] == "0.3"
    assert _tp_frac_from_plan(plan) == Decimal("0.3")


def test_upsert_plan_preserves_tp1_fraction_on_followup_write(tmp_path):
    path = tmp_path / "positions.json"
    upsert_plan(
        "ETH-USDT",
        side="short",
        qty="5",
        entry="2000",
        stop="2050",
        tp="1925",
        tp1="1925",
        tp_frac="0.3",
        path=path,
    )

    upsert_plan(
        "ETH-USDT",
        side="short",
        qty="3.5",
        entry="2000",
        stop="2000",
        be_moved=True,
        path=path,
    )

    plan = get_plan("ETH-USDT", path, side="short")

    assert plan is not None
    assert plan["tp_frac"] == "0.3"
    assert _tp_frac_from_plan(plan) == Decimal("0.3")


@pytest.mark.parametrize("invalid_fraction", ["0", "1", "-0.1", "nan", "inf"])
def test_upsert_plan_rejects_invalid_tp1_fraction(tmp_path, invalid_fraction):
    with pytest.raises(ValueError, match="tp_frac"):
        upsert_plan(
            "BTC-USDT",
            side="long",
            qty="1",
            entry="100",
            stop="98",
            tp1="103",
            tp_frac=invalid_fraction,
            path=tmp_path / "positions.json",
        )


def test_tp1_leg_estimate_uses_configured_fraction_for_long_and_short():
    long_plan = {"qty": "100", "tp1": "110", "tp_frac": "0.3"}
    short_plan = {"qty": "100", "tp1": "90", "tp_frac": "0.3"}

    assert _estimate_tp1_leg(long_plan, 100, "long", 100) == pytest.approx(300)
    assert _estimate_tp1_leg(short_plan, 100, "short", 100) == pytest.approx(300)


def test_legacy_plan_without_tp1_fraction_uses_legacy_default():
    plan = {"qty": "100", "tp1": "110"}

    assert _estimate_tp1_leg(plan, 100, "long", 100) == pytest.approx(300)
