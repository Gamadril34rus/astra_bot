from decimal import Decimal
from types import SimpleNamespace

import pytest
from astra_bot.decision.context import SignalCandidate
from astra_bot.decision.meta_strategy import candidate_prior_r
from astra_bot.decision.trading_engine import TradingEngine


class Strategy:
    def __init__(self, name):
        self.name = name


def test_kill_switch_counts_only_any_bucket(tmp_path, caplog):
    caplog.set_level("INFO")
    stats = tmp_path / "stats.json"
    row = {"sample_size": 1, "wins_sum_r": 0.0, "losses_sum_r": -1.0}
    stats.write_text(__import__("json").dumps({"buckets": {
        "bad|ANY|1h": row,
        "bad|LOW_VOLATILITY|1h": row,
        "bad|T:RANGE/V:LOW/L:NORMAL|1h": row,
    }}))
    engine = TradingEngine.__new__(TradingEngine)
    engine.config = SimpleNamespace(stats_path=str(stats))
    engine.pipeline = SimpleNamespace(strategies=[Strategy("bad")])

    assert engine.apply_kill_switches_from_stats() == []
    assert [s.name for s in engine.pipeline.strategies] == ["bad"]
    assert "n_any=" in caplog.text


def test_seven_killed_and_twenty_four_false_kills_return(tmp_path):
    stats = tmp_path / "stats.json"
    buckets = {}
    names = []
    for i in range(7):
        name = f"loser_{i}"
        names.append(name)
        buckets[f"{name}|ANY|1h"] = {
            "sample_size": 5, "wins_sum_r": 0.0, "losses_sum_r": -5.0
        }
    for i in range(24):
        name = f"returned_{i}"
        names.append(name)
        # The old implementation falsely promoted regime copies to n>=5.
        buckets[f"{name}|ANY|1h"] = {
            "sample_size": 1, "wins_sum_r": 0.0, "losses_sum_r": -1.0
        }
        buckets[f"{name}|LOW_VOL|1h"] = {
            "sample_size": 4, "wins_sum_r": 0.0, "losses_sum_r": -4.0
        }
    stats.write_text(__import__("json").dumps({"buckets": buckets}))
    engine = TradingEngine.__new__(TradingEngine)
    engine.config = SimpleNamespace(stats_path=str(stats))
    engine.pipeline = SimpleNamespace(strategies=[Strategy(name) for name in names])

    assert len(engine.apply_kill_switches_from_stats()) == 7
    assert len(engine.pipeline.strategies) == 24
    assert all(strategy.name.startswith("returned_") for strategy in engine.pipeline.strategies)


def test_kill_switch_keeps_rule_n_five_pf_below_one(tmp_path):
    stats = tmp_path / "stats.json"
    stats.write_text(__import__("json").dumps({"buckets": {
        "bad|ANY|1h": {"sample_size": 5, "wins_sum_r": 1.0, "losses_sum_r": -2.0},
        "young|ANY|1h": {"sample_size": 4, "wins_sum_r": 0.0, "losses_sum_r": -4.0},
    }}))
    engine = TradingEngine.__new__(TradingEngine)
    engine.config = SimpleNamespace(stats_path=str(stats))
    engine.pipeline = SimpleNamespace(strategies=[Strategy("bad"), Strategy("young")])

    assert engine.apply_kill_switches_from_stats() == ["bad"]
    assert [s.name for s in engine.pipeline.strategies] == ["young"]


def test_probation_applied_after_sizer(monkeypatch):
    monkeypatch.setattr(
        "astra_bot.engines.position_sizer.calculate_position_size",
        lambda **kwargs: Decimal("8"),
    )
    engine = TradingEngine.__new__(TradingEngine)
    engine.config = SimpleNamespace(
        risk_per_trade_pct=Decimal("0.01"), max_notional_pct=Decimal("0.10")
    )
    engine.stats_store = SimpleNamespace(get_any=lambda strategy, timeframe: None)

    assert engine._position_size(
        Decimal("1000"), Decimal("100"), Decimal("90"),
        strategy="new", timeframe="1h",
    ) == Decimal("2")

    engine.stats_store = SimpleNamespace(
        get_any=lambda strategy, timeframe: SimpleNamespace(
            sample_size=5, win_rate=0.5, avg_win_r=1.0, avg_loss_r=-1.0
        )
    )
    assert engine._position_size(
        Decimal("1000"), Decimal("100"), Decimal("90"),
        strategy="old", timeframe="1h",
    ) == Decimal("8")


def test_prior_charges_both_sides_with_paper_defaults():
    candidate = SignalCandidate(
        symbol="BTC-USDT", direction="long", entry_price=Decimal("100"),
        stop_loss=Decimal("99"), take_profit=Decimal("102"), timeframe="1h",
        strategy="test", confidence=0.6, ml_probability=0.6,
    )
    # gross EV = .6*2 - .4 = .8; round trip costs=(.0005+.001)*2/.01=.3R
    assert candidate_prior_r(candidate) == pytest.approx(0.5)
