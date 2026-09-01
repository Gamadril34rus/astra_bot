"""Unit tests for the unified CostModel (P0-1).

Проверяем:
- effective entry/exit prices с slippage;
- fees на вход/выход round-trip;
- net PnL;
- инвариант: total_fees ≥ 2 × notional × min_fee_rate;
- backward-compat через cost_model_from_flat.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from astra_bot.engines.cost_model import CostModel, cost_model_from_flat


# ---------- effective prices ----------


class TestEffectivePrices:
    def test_long_entry_slippage_up(self):
        cm = CostModel(slippage_pct=Decimal("0.001"))
        # Long: вход дороже на 0.1%.
        assert cm.effective_entry_price(Decimal("100"), "long") == Decimal("100.1")

    def test_long_exit_slippage_down(self):
        cm = CostModel(slippage_pct=Decimal("0.001"))
        # Long: выход дешевле на 0.1%.
        assert cm.effective_exit_price(Decimal("100"), "long") == Decimal("99.9")

    def test_short_entry_slippage_down(self):
        cm = CostModel(slippage_pct=Decimal("0.001"))
        assert cm.effective_entry_price(Decimal("100"), "short") == Decimal("99.9")

    def test_short_exit_slippage_up(self):
        cm = CostModel(slippage_pct=Decimal("0.001"))
        assert cm.effective_exit_price(Decimal("100"), "short") == Decimal("100.1")

    def test_buy_alias_for_long(self):
        cm = CostModel(slippage_pct=Decimal("0.001"))
        assert cm.effective_entry_price(Decimal("100"), "buy") == Decimal("100.1")

    def test_sell_alias_for_short(self):
        cm = CostModel(slippage_pct=Decimal("0.001"))
        assert cm.effective_entry_price(Decimal("100"), "sell") == Decimal("99.9")


# ---------- fees ----------


class TestFees:
    def test_entry_fee_long(self):
        cm = CostModel(taker_fee_rate=Decimal("0.001"), slippage_pct=Decimal("0.001"))
        # fill = 100.1, fee = 100.1 * 1 * 0.001 = 0.1001
        assert cm.entry_fee(Decimal("100"), Decimal("1"), "long") == pytest.approx(
            Decimal("0.1001")
        )

    def test_exit_fee_long(self):
        cm = CostModel(taker_fee_rate=Decimal("0.001"), slippage_pct=Decimal("0.001"))
        # fill = 99.9, fee = 99.9 * 1 * 0.001 = 0.0999
        assert cm.exit_fee(Decimal("100"), Decimal("1"), "long") == pytest.approx(
            Decimal("0.0999")
        )

    def test_round_trip_fees_long(self):
        cm = CostModel(taker_fee_rate=Decimal("0.001"), slippage_pct=Decimal("0.001"))
        # entry_fee = 100.1 * 1 * 0.001 = 0.1001
        # exit at 99: fill = 98.901, exit_fee = 98.901 * 1 * 0.001 = 0.098901
        # total = 0.199001
        rt = cm.round_trip_fees(Decimal("100"), Decimal("99"), Decimal("1"), "long")
        assert rt == pytest.approx(Decimal("0.199001"), abs=Decimal("1e-6"))

    def test_maker_fee_lower_than_taker(self):
        cm = CostModel(
            taker_fee_rate=Decimal("0.001"),
            maker_fee_rate=Decimal("0.0005"),
            slippage_pct=Decimal("0"),
        )
        # Maker fee at 100 * 1 * 0.0005 = 0.05
        assert cm.entry_fee(Decimal("100"), Decimal("1"), "long", is_maker=True) == pytest.approx(
            Decimal("0.05")
        )
        # Taker fee at 100 * 1 * 0.001 = 0.1
        assert cm.entry_fee(Decimal("100"), Decimal("1"), "long", is_maker=False) == pytest.approx(
            Decimal("0.1")
        )


# ---------- net PnL ----------


class TestNetPnl:
    def test_net_pnl_long_stop_loss(self):
        """Long 100, exit 99, fee 0.1%, slippage 0.1%.

        entry_fill = 100.1, exit_fill = 98.901
        gross = (98.901 - 100.1) * 1 = -1.199
        fees = 100.1*0.001 + 98.901*0.001 = 0.199001
        net = -1.199 - 0.199001 = -1.398001
        """
        cm = CostModel(taker_fee_rate=Decimal("0.001"), slippage_pct=Decimal("0.001"))
        pnl, fees = cm.net_pnl(Decimal("100"), Decimal("99"), Decimal("1"), "long")
        assert pnl == pytest.approx(Decimal("-1.398001"), abs=Decimal("1e-6"))
        assert fees == pytest.approx(Decimal("0.2000"), abs=Decimal("1e-3"))

    def test_net_pnl_short_profit(self):
        """Short 100, exit 95, fee 0.1%, slippage 0.1%.

        entry_fill = 99.9, exit_fill = 95.095
        gross = (99.9 - 95.095) * 1 = 4.805
        fees = 99.9*0.001 + 95.095*0.001 = 0.195 (approximately)
        net = 4.805 - 0.195 ≈ 4.61
        """
        cm = CostModel(taker_fee_rate=Decimal("0.001"), slippage_pct=Decimal("0.001"))
        pnl, fees = cm.net_pnl(Decimal("100"), Decimal("95"), Decimal("1"), "short")
        assert pnl > Decimal("4")
        assert fees > Decimal("0.19")


# ---------- Invariant ----------


class TestInvariant:
    def test_invariant_holds_with_proper_fees(self):
        """Round-trip с commission 0.1% + slippage 0.1% на сторону."""
        cm = CostModel(taker_fee_rate=Decimal("0.001"), slippage_pct=Decimal("0.001"))
        # Entry: fill=100.1, fee=0.1001; Exit at 99: fill=98.901, fee=0.098901
        e_fee = cm.entry_fee(Decimal("100"), Decimal("1"), "long")
        x_fee = cm.exit_fee(Decimal("99"), Decimal("1"), "long")
        e_notional = Decimal("100.1")  # 100 * 1.001
        x_notional = Decimal("98.901")  # 99 * 0.999
        assert cm.check_round_trip_invariant(
            e_fee, x_fee, e_notional, x_notional, cm.min_fee_rate
        )

    def test_invariant_violates_with_zero_fees(self):
        """Нулевые комиссии нарушают инвариант."""
        zero = Decimal("0")
        notional = Decimal("100")
        min_rate = Decimal("0.001")
        assert not CostModel.check_round_trip_invariant(
            zero, zero, notional, notional, min_rate
        )

    def test_invariant_violates_when_one_side_zero(self):
        """Если одна из сторон = 0 — инвариант нарушен."""
        min_rate = Decimal("0.001")
        notional = Decimal("100")
        fee = notional * min_rate  # = 0.1
        # Entry charged, exit = 0
        assert not CostModel.check_round_trip_invariant(
            fee, Decimal("0"), notional, notional, min_rate
        )
        # Entry = 0, exit charged
        assert not CostModel.check_round_trip_invariant(
            Decimal("0"), fee, notional, notional, min_rate
        )

    def test_assert_invariant_raises_on_zero_taker_fee(self):
        """CostModel с нулевой taker_fee не создаётся (TZ P0-1: запрет fees=0)."""
        with pytest.raises(ValueError, match="taker_fee_rate must be > 0"):
            CostModel(taker_fee_rate=Decimal("0"), slippage_pct=Decimal("0.001"))

    def test_assert_invariant_passes_with_fees(self):
        cm = CostModel(taker_fee_rate=Decimal("0.001"), slippage_pct=Decimal("0.001"))
        # Should not raise — both sides charged at 0.1%
        cm.assert_invariant(Decimal("100"), Decimal("99"), Decimal("1"), "long")

    def test_invariant_per_side_check(self):
        """Каждая сторона проверяется независимо."""
        notional = Decimal("1000")
        min_rate = Decimal("0.001")
        fee = notional * min_rate  # = 1.0
        # Both sides at exactly minimum → pass.
        assert CostModel.check_round_trip_invariant(
            fee, fee, notional, notional, min_rate
        )
        # One side below minimum → fail.
        assert not CostModel.check_round_trip_invariant(
            fee - Decimal("0.01"), fee, notional, notional, min_rate
        )


# ---------- cost_model_from_flat ----------


class TestFromFlat:
    def test_flat_creates_correct_model(self):
        cm = cost_model_from_flat(
            fee_pct=Decimal("0.002"),
            slippage_pct=Decimal("0.0005"),
        )
        assert cm.taker_fee_rate == Decimal("0.002")
        assert cm.maker_fee_rate == Decimal("0.002")
        assert cm.slippage_pct == Decimal("0.0005")

    def test_flat_default(self):
        cm = cost_model_from_flat()
        assert cm.taker_fee_rate == Decimal("0.001")
        assert cm.slippage_pct == Decimal("0.001")


# ---------- min_fee_rate property ----------


class TestMinFeeRate:
    def test_min_fee_rate_is_min_of_maker_taker(self):
        cm = CostModel(maker_fee_rate=Decimal("0.0005"), taker_fee_rate=Decimal("0.001"))
        assert cm.min_fee_rate == Decimal("0.0005")

    def test_min_fee_rate_equal(self):
        cm = CostModel(maker_fee_rate=Decimal("0.001"), taker_fee_rate=Decimal("0.001"))
        assert cm.min_fee_rate == Decimal("0.001")
