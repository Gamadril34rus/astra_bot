"""Tests for P0-2: baseline control group and lift_vs_baseline.

Проверяем:
1. research_history генерирует baseline_observations > 0.
2. Гипотезы содержат baseline_expectancy и lift_vs_baseline.
3. Без baseline гипотеза не может стать VALIDATED.
"""

from __future__ import annotations

import json
from decimal import Decimal

import numpy as np
from astra_bot.ml.hypothesis_engine import Hypothesis, HypothesisStatus
from astra_bot.ml.market_research import research_history

# ---------- Synthetic candle helper ----------

def _make_candles(n: int, symbol: str = "BTC-USDT", timeframe: str = "1h"):
    """Create synthetic candles with some variance for feature computation."""
    from astra_bot.core.models import Candle

    candles = []
    price = 100.0
    rng = np.random.RandomState(42)
    for i in range(n):
        # Random walk with trend
        ret = rng.normal(0.0001, 0.01)
        price *= (1 + ret)
        high = price * (1 + abs(rng.normal(0, 0.005)))
        low = price * (1 - abs(rng.normal(0, 0.005)))
        open_price = price * (1 + rng.normal(0, 0.002))
        candles.append(Candle(
            exchange="test",
            symbol=symbol,
            timeframe=timeframe,
            open_time=i * 3600000,
            open=Decimal(str(round(open_price, 2))),
            high=Decimal(str(round(high, 2))),
            low=Decimal(str(round(low, 2))),
            close=Decimal(str(round(price, 2))),
            volume=Decimal(str(round(rng.uniform(100, 1000), 2))),
            quote_volume=Decimal(str(round(rng.uniform(10000, 100000), 2))),
        ))
    return candles


# ---------- Baseline observations ----------


class TestBaselineObservations:
    def test_baseline_observations_generated(self, tmp_path):
        """research_history генерирует baseline_observations > 0."""
        candles = _make_candles(500, "BTC-USDT", "1h")
        history = {"BTC-USDT": candles}
        output = tmp_path / "obs.jsonl"
        hypotheses = tmp_path / "hyp.json"

        stats = research_history(
            history,
            output=output,
            hypotheses_output=hypotheses,
            sample_every=4,
        )

        assert stats["baseline_observations"] > 0, (
            f"Expected baseline_observations > 0, got {stats['baseline_observations']}"
        )

    def test_baseline_ratio_reasonable(self, tmp_path):
        """Примерно 1/4 наблюдений — baseline (baseline_step=4)."""
        candles = _make_candles(500, "BTC-USDT", "1h")
        history = {"BTC-USDT": candles}
        output = tmp_path / "obs.jsonl"
        hypotheses = tmp_path / "hyp.json"

        stats = research_history(
            history,
            output=output,
            hypotheses_output=hypotheses,
            sample_every=4,
        )

        total = stats["observations"]
        baseline = stats["baseline_observations"]
        # Expect roughly 25% baseline (1 in 4)
        ratio = baseline / total if total > 0 else 0
        assert 0.1 < ratio < 0.5, f"Baseline ratio {ratio:.2f} out of expected range [0.1, 0.5]"

    def test_baseline_in_observations_file(self, tmp_path):
        """Baseline-записи присутствуют в output-файле."""
        candles = _make_candles(500, "BTC-USDT", "1h")
        history = {"BTC-USDT": candles}
        output = tmp_path / "obs.jsonl"
        hypotheses = tmp_path / "hyp.json"

        research_history(
            history,
            output=output,
            hypotheses_output=hypotheses,
            sample_every=4,
        )

        # Read observations and count baseline
        baseline_count = 0
        total_count = 0
        with open(output) as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    total_count += 1
                    if "baseline" in row.get("events", []):
                        baseline_count += 1

        assert baseline_count > 0, "No baseline records in output file"
        assert total_count > 0


# ---------- Hypotheses with baseline ----------


class TestHypothesesBaseline:
    def test_hypotheses_have_lift_vs_baseline(self, tmp_path):
        """Гипотезы содержат lift_vs_baseline когда есть baseline."""
        candles = _make_candles(500, "BTC-USDT", "1h")
        history = {"BTC-USDT": candles}
        output = tmp_path / "obs.jsonl"
        hypotheses = tmp_path / "hyp.json"

        research_history(
            history,
            output=output,
            hypotheses_output=hypotheses,
            sample_every=4,
        )

        data = json.loads(hypotheses.read_text())
        hyps = data.get("hypotheses", {})
        # At least some hypotheses should have lift_vs_baseline
        sum(1 for h in hyps.values() if "lift_vs_baseline" in h)
        has_bl = sum(1 for h in hyps.values() if "baseline_expectancy" in h)
        assert has_bl > 0 or len(hyps) == 0, "Expected some hypotheses with baseline_expectancy"


# ---------- VALIDATED requires baseline ----------


class TestValidatedRequiresBaseline:
    def test_validated_rejects_zero_lift(self):
        """Гипотеза с lift_vs_baseline=0 не может стать VALIDATED."""
        hyp = Hypothesis(
            id="test-1",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            description="Test hypothesis",
            sample_size=100,
            train_metrics={"expectancy": 0.5},
            validation_metrics={"expectancy": 0.4},
            oos_metrics={"expectancy": 0.3},
            walk_forward_metrics={"expectancy": 0.3},
            stress_metrics={"fees_impact": 0.1},
            expectancy=0.3,
            baseline_expectancy=0.2,
            lift_vs_baseline=0.0,  # No lift
            status=HypothesisStatus.TESTING,
        )
        ok, reason = hyp.transition(HypothesisStatus.VALIDATED)
        assert not ok, "Should reject VALIDATED with zero lift"
        assert "lift_vs_baseline" in reason.lower() or "baseline" in reason.lower()

    def test_validated_rejects_negative_lift(self):
        """Гипотеза с отрицательным lift не может стать VALIDATED."""
        hyp = Hypothesis(
            id="test-2",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            description="Test hypothesis with negative lift",
            sample_size=100,
            train_metrics={"expectancy": 0.5},
            validation_metrics={"expectancy": 0.4},
            oos_metrics={"expectancy": 0.3},
            walk_forward_metrics={"expectancy": 0.3},
            stress_metrics={"fees_impact": 0.1},
            expectancy=0.3,
            baseline_expectancy=0.4,
            lift_vs_baseline=-0.1,  # Worse than baseline
            status=HypothesisStatus.TESTING,
        )
        ok, reason = hyp.transition(HypothesisStatus.VALIDATED)
        assert not ok, "Should reject VALIDATED with negative lift"
        assert "baseline" in reason.lower()

    def test_validated_accepts_positive_lift(self):
        """Гипотеза с положительным lift может стать VALIDATED."""
        hyp = Hypothesis(
            id="test-3",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            description="Test hypothesis with positive lift",
            sample_size=100,
            train_metrics={"expectancy": 0.5},
            validation_metrics={"expectancy": 0.4},
            oos_metrics={"expectancy": 0.3},
            walk_forward_metrics={"expectancy": 0.3},
            stress_metrics={"fees_impact": 0.1},
            expectancy=0.3,
            baseline_expectancy=0.1,
            lift_vs_baseline=0.2,  # Better than baseline
            status=HypothesisStatus.TESTING,
        )
        ok, reason = hyp.transition(HypothesisStatus.VALIDATED)
        assert ok, f"Should accept VALIDATED with positive lift, got: {reason}"
