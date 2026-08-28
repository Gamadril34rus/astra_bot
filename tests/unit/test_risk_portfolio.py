"""Risk Engine portfolio-слой (Этап 5): бета, gross/net/группы.

Дефолты НЕ ослабляют и НЕ меняют текущее поведение: новые лимиты по
умолчанию равны max_exposure_pct; бета BTC = 1.0. Конфиг может только
ужесточать (survival > returns).
"""

from __future__ import annotations

from decimal import Decimal

from astra_bot.engines.risk_engine import RiskConfig, RiskEngine

EQUITY = Decimal("10000")
PRICE = Decimal("100")
STOP = Decimal("99")  # расстояние 1


def _engine(**cfg_overrides) -> RiskEngine:
    base = dict(
        risk_per_trade=Decimal("0.004"),
        max_open_positions=10,
        max_exposure_pct=Decimal("0.30"),
    )
    base.update(cfg_overrides)
    eng = RiskEngine(RiskConfig(**base))
    eng.set_capital(EQUITY, EQUITY)  # dd = 0 → multiplier = 1
    return eng


class TestBeta:
    def test_btc_beta_unchanged(self):
        eng = _engine()
        r = eng.check_trade(
            "BTC-USDT", "long", PRICE, STOP, Decimal("110"),
            Decimal("30"), "t",
        )
        # 0.004 × 10000 = 40 ≥ 30 × 1 → без уменьшения
        assert r.approved is True

    def test_alt_beta_reduces_size(self):
        eng = _engine()  # ETH beta = 1.4
        r = eng.check_trade(
            "ETH-USDT", "long", PRICE, STOP, Decimal("110"),
            Decimal("30"), "t",
        )
        assert r.approved is False
        # 40 / 1.4 ≈ 28.5714
        expected = Decimal("40") / Decimal("1.4")
        assert r.details["adjusted_size"] == expected

    def test_unknown_symbol_uses_default_beta(self):
        eng = _engine(default_beta=Decimal("2.0"))
        r = eng.check_trade(
            "FOO-USDT", "long", PRICE, STOP, Decimal("110"),
            Decimal("20"), "t",
        )
        # 40 / 2.0 = 20 → ровно на пределе → approved
        assert r.approved is True
        r2 = eng.check_trade(
            "FOO-USDT", "long", PRICE, STOP, Decimal("110"),
            Decimal("21"), "t",
        )
        assert r2.approved is False
        assert r2.details["adjusted_size"] == Decimal("20")

    def test_beta_never_increases_size(self):
        """Бета < 1 невозможна: max(1, beta)."""
        eng = _engine(betas={"BTC": Decimal("0.5")})
        r = eng.check_trade(
            "BTC-USDT", "long", PRICE, STOP, Decimal("110"),
            Decimal("30"), "t",
        )
        assert r.approved is True  # 30 ≤ 40 — без «увеличения»


class TestNetExposure:
    def test_net_cap_reduces_same_direction(self):
        eng = _engine(max_net_exposure_pct=Decimal("0.10"))
        eng.add_position("p1", symbol="BTC-USDT", side="long",
                         notional=Decimal("800"))
        r = eng.check_trade(
            "ETH-USDT", "long", PRICE, STOP, Decimal("110"),
            Decimal("5"), "t",  # notional 500
        )
        # net = 800 + 500 = 1300 > 1000 → allowed_net = 200 → size 2
        assert r.approved is False
        assert r.details["adjusted_size"] == Decimal("2")
        assert "Net exposure" in r.reason

    def test_net_cap_allows_hedged(self):
        """Короткая позиция против длинной НЕ бьёт net-лимит."""
        eng = _engine(max_net_exposure_pct=Decimal("0.10"))
        eng.add_position("p1", symbol="BTC-USDT", side="long",
                         notional=Decimal("900"))
        # Для шорта стоп ВЫШЕ цены (101), тейк ниже.
        r = eng.check_trade(
            "ETH-USDT", "short", PRICE, Decimal("101"), Decimal("90"),
            Decimal("3"), "t",  # notional 300, net = 900 − 300
        )
        assert r.approved is True

    def test_net_cap_blocked_when_full(self):
        eng = _engine(max_net_exposure_pct=Decimal("0.10"))
        eng.add_position("p1", symbol="BTC-USDT", side="long",
                         notional=Decimal("1000"))
        r = eng.check_trade(
            "ETH-USDT", "long", PRICE, STOP, Decimal("110"),
            Decimal("1"), "t",
        )
        assert r.approved is False
        assert r.details.get("adjusted_size") is None


class TestCorrelationGroup:
    def test_group_cap_binds_when_net_is_small(self):
        """Длинная + короткая в одной группе: net мал, группа бьёт."""
        eng = _engine(max_group_exposure_pct=Decimal("0.20"))
        eng.add_position("p1", symbol="BTC-USDT", side="long",
                         notional=Decimal("900"))
        eng.add_position("p2", symbol="ETH-USDT", side="short",
                         notional=Decimal("900"))
        # net = 0 (hedged), группа crypto = 1800; cap = 2000
        r = eng.check_trade(
            "SOL-USDT", "long", PRICE, STOP, Decimal("110"),
            Decimal("2"), "t",  # notional 200 → группа 2000 — впритык
        )
        assert r.approved is True
        r2 = eng.check_trade(
            "SOL-USDT", "long", PRICE, STOP, Decimal("110"),
            Decimal("2.5"), "t",  # 250 → 2050 > 2000
        )
        assert r2.approved is False
        assert r2.details["adjusted_size"] == Decimal("2")
        assert "group" in (r2.reason or "").lower()

    def test_groups_isolate_symbols(self):
        eng = _engine(
            max_group_exposure_pct=Decimal("0.10"),
            correlation_groups={"BTC-USDT": "majors", "ETH-USDT": "alts"},
        )
        eng.add_position("p1", symbol="BTC-USDT", side="long",
                         notional=Decimal("950"))
        # ETH в другой группе — лимит группы alts пуст
        r = eng.check_trade(
            "ETH-USDT", "long", PRICE, STOP, Decimal("110"),
            Decimal("5"), "t",  # 500 ≤ 1000
        )
        assert r.approved is True


class TestGrossAndDefaults:
    def test_gross_cap_can_be_stricter(self):
        eng = _engine(max_gross_exposure_pct=Decimal("0.10"))
        eng.add_position("p1", symbol="BTC-USDT", side="long",
                         notional=Decimal("800"))
        r = eng.check_trade(
            "ETH-USDT", "long", PRICE, STOP, Decimal("110"),
            Decimal("3"), "t",  # 800 + 300 = 1100 > 1000
        )
        assert r.approved is False
        assert r.details["adjusted_size"] == Decimal("2")

    def test_defaults_do_not_change_behavior(self):
        """Дефолты: gross/net/group = 0.30 → впритык 3000 проходит."""
        eng = _engine()
        eng.add_position("p1", symbol="BTC-USDT", side="long",
                         notional=Decimal("2900"))
        r = eng.check_trade(
            "BTC-USDT", "long", PRICE, STOP, Decimal("110"),
            Decimal("1"), "t",  # gross 3000 = cap
        )
        assert r.approved is True
        # 3001 → gross-лимит (прежнее поведение max_exposure_pct)
        r2 = eng.check_trade(
            "BTC-USDT", "long", PRICE, Decimal("99.99"), Decimal("110"),
            Decimal("1.01"), "t",
        )
        assert r2.approved is False
        assert "Exposure" in r2.reason

    def test_str_position_without_meta_counts_only(self):
        """Старый вызов add_position(id) не ломает проверки."""
        eng = _engine()
        eng.add_position("legacy-id")
        r = eng.check_trade(
            "BTC-USDT", "long", PRICE, STOP, Decimal("110"),
            Decimal("1"), "t",
        )
        assert r.approved is True

    def test_remove_position_clears_meta(self):
        eng = _engine()
        eng.add_position("p1", symbol="BTC-USDT", side="long",
                         notional=Decimal("900"))
        eng.remove_position("p1")
        expo = eng._portfolio_exposures()
        assert expo["gross"] == 0
        assert "p1" not in eng._open_meta
