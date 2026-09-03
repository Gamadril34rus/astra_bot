"""Sizing-инварианты Risk Engine (Этап 8).

Свойства, которые должны держаться для ЛЮБЫХ параметров (seeded-
случайные проверки): риск-бюджет, монотонность, экспозиция, типы.
"""

from __future__ import annotations

import random
from decimal import Decimal

import pytest
from astra_bot.engines.risk_engine import RiskConfig, RiskEngine

R = random.Random(42)


def _cfg(**kw) -> RiskConfig:
    base = dict(
        risk_per_trade=Decimal("0.004"),
        max_open_positions=100,
        max_exposure_pct=Decimal("10.0"),  # широкий: риск-бюджет строже
    )
    base.update(kw)
    return RiskConfig(**base)


def _eng(**kw) -> RiskEngine:
    eng = RiskEngine(_cfg(**kw))
    eng.set_capital(Decimal("10000"), Decimal("10000"))
    return eng


class TestRiskBudgetInvariant:
    @pytest.mark.parametrize("i", range(40))
    def test_adjusted_size_never_exceeds_risk_budget(self, i):
        """adjusted_size × stop_distance <= equity × risk_per_trade × mult."""
        eng = _eng()
        price = Decimal(str(round(R.uniform(10, 10000), 2)))
        stop_pct = Decimal(str(round(R.uniform(0.005, 0.05), 4)))
        stop = price * (1 - stop_pct)
        # Крупный proposed — почти всегда упрётся в бюджет.
        proposed = Decimal(str(round(R.uniform(500, 5000), 2)))
        r = eng.check_trade(
            "BTC-USDT", "long", price, stop, price * Decimal("1.02"),
            proposed, "t",
        )
        if r.approved:
            return  # approved ⇒ budget тривиально соблюдён
        adj = r.details.get("adjusted_size")
        assert adj is not None, r.reason
        budget = Decimal("10000") * Decimal("0.004")
        actual = adj * abs(price - stop)
        assert actual <= budget + Decimal("0.01"), (
            f"i={i}: {actual} > {budget} (beta=1.0)"
        )

    @pytest.mark.parametrize("i", range(20))
    def test_size_monotonic_in_stop_distance(self, i):
        """Теснее стоп (меньше stop_distance) => размер не меньше."""
        price = Decimal(str(round(R.uniform(10, 5000), 2)))
        wide = price * Decimal("0.95")   # stop 5%
        tight = price * Decimal("0.99")  # stop 1%
        eng_w = _eng()
        eng_t = _eng()
        r_w = eng_w.check_trade(
            "BTC-USDT", "long", price, wide, price * Decimal("1.05"),
            Decimal("1000000"), "t",
        )
        r_t = eng_t.check_trade(
            "BTC-USDT", "long", price, tight, price * Decimal("1.05"),
            Decimal("1000000"), "t",
        )
        adj_w = r_w.details.get("adjusted_size") or Decimal(0)
        adj_t = r_t.details.get("adjusted_size") or Decimal(0)
        assert adj_t >= adj_w - Decimal("0.000001")

    def test_adjusted_size_is_decimal(self):
        """Денежная логика — Decimal, не float (TZ)."""
        eng = _eng()
        r = eng.check_trade(
            "BTC-USDT", "long", Decimal("100"), Decimal("99"),
            Decimal("105"), Decimal("100000"), "t",
        )
        assert isinstance(r.details["adjusted_size"], Decimal)

    def test_beta_caps_high_beta_symbols(self):
        """Высокобетовый символ получает размер <= BTC-размера."""
        eng_btc = _eng()
        eng_sol = _eng()
        proposed = Decimal("100000")
        r_btc = eng_btc.check_trade(
            "BTC-USDT", "long", Decimal("100"), Decimal("99"),
            Decimal("105"), proposed, "t",
        )
        r_sol = eng_sol.check_trade(
            "SOL-USDT", "long", Decimal("100"), Decimal("99"),
            Decimal("105"), proposed, "t",
        )
        adj_btc = r_btc.details.get("adjusted_size")
        adj_sol = r_sol.details.get("adjusted_size")
        assert adj_sol is not None and adj_btc is not None
        assert adj_sol <= adj_btc

    def test_no_negative_size(self):
        eng = _eng()
        r = eng.check_trade(
            "BTC-USDT", "long", Decimal("100"), Decimal("99"),
            Decimal("105"), Decimal("0.0001"), "t",
        )
        size = r.details.get("adjusted_size")
        if size is not None:
            assert size >= 0


class TestExposureInvariants:
    def test_exposure_adjustment_fits_cap(self):
        """После adjusted_size gross <= cap."""
        eng = _eng(max_exposure_pct=Decimal("0.30"))
        eng.add_position("p1", symbol="BTC-USDT", side="long",
                         notional=Decimal("2500"))  # cap = 3000
        r = eng.check_trade(
            "ETH-USDT", "long", Decimal("100"), Decimal("99"),
            Decimal("105"), Decimal("20"), "t",  # +2000 → 4500 > 3000
        )
        assert not r.approved
        adj = r.details["adjusted_size"]
        assert 2500 + adj * Decimal("100") <= Decimal("3000") + Decimal("0.01")

    def test_str_position_meta_consistent_with_cap(self):
        """Старый вызов add_position(id) не ломает экспозицию."""
        eng = _eng(max_exposure_pct=Decimal("0.30"))
        eng.add_position("legacy")
        r = eng.check_trade(
            "BTC-USDT", "long", Decimal("100"), Decimal("99"),
            Decimal("105"), Decimal("10"), "t",
        )
        assert r.approved  # legacy без meta = 0 номинала, но не crash
