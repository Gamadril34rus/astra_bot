"""Model Registry Phase 4: promotion chain, A/B, stress, rollback (TZ §18-23)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from astra_bot.ml.model_registry import ModelRegistry
from astra_bot.ml.model_trainer import MLModel, ModelMetrics, TrainingConfig


class DummyModel:
    def predict(self, x):
        return np.zeros(len(x))

    def predict_proba(self, x):
        return np.full((len(x), 2), 0.5)


def _make_model() -> MLModel:
    return MLModel(
        model=DummyModel(),
        config=TrainingConfig(),
        metrics=ModelMetrics(accuracy=0.6, precision=0.55, recall=0.5,
                             f1_score=0.52, roc_auc=0.6),
        feature_names=["a", "b"],
    )


@pytest.fixture()
def registry(tmp_path: Path) -> ModelRegistry:
    return ModelRegistry(registry_dir=str(tmp_path / "registry"))


def _register_evaluated(reg: ModelRegistry, version: str, **kw) -> None:
    model = _make_model()
    reg.register(model, version=version, description=f"test {version}")
    reg.set_evaluation(version, sample_size=kw.pop("n", 100), **kw)


def _promote_to_validated(reg: ModelRegistry, version: str) -> None:
    ok, why = reg.promote(version, "validated")
    assert ok, why


def _promote_to_production(reg: ModelRegistry, version: str, **ev) -> None:
    reg.set_stress_metrics(version, {"fees_x2": 0.1, "slippage_x3": 0.08,
                                     "stable": True})
    ok, why = reg.promote(version, "production", evidence=ev or None)
    assert ok, why


class TestPromotionChain:
    def test_sample_size_gate(self, registry):
        reg = registry
        model = _make_model()
        reg.register(model, version="ML-001")
        ok, why = reg.promote("ML-001", "validated")
        assert not ok
        assert "sample_size" in why

    def test_full_chain(self, registry):
        reg = registry
        _register_evaluated(reg, "ML-001", expectancy=0.2,
                            oos_expectancy=0.15, walk_forward_expectancy=0.12)
        _promote_to_validated(reg, "ML-001")
        assert reg.get_model("ML-001").status == "validated"
        _promote_to_production(reg, "ML-001")
        assert reg.get_model("ML-001").status == "production"
        assert reg.get_production_model().version == "ML-001"
        # История статусов сохранена.
        log = reg.get_model("ML-001").status_log
        assert [e["status"] for e in log] == ["validated", "production"]

    def test_oos_gate(self, registry):
        reg = registry
        _register_evaluated(reg, "ML-001", expectancy=0.2,
                            oos_expectancy=-0.05, walk_forward_expectancy=0.1)
        _promote_to_validated(reg, "ML-001")
        reg.set_stress_metrics("ML-001", {"stable": True})
        ok, why = reg.promote("ML-001", "production")
        assert not ok
        assert "OOS" in why

    def test_walk_forward_gate(self, registry):
        reg = registry
        _register_evaluated(reg, "ML-001", expectancy=0.2,
                            oos_expectancy=0.1, walk_forward_expectancy=-0.01)
        _promote_to_validated(reg, "ML-001")
        reg.set_stress_metrics("ML-001", {"stable": True})
        ok, why = reg.promote("ML-001", "production")
        assert not ok
        assert "walk-forward" in why

    def test_unstable_stress_never_active(self, registry):
        """TZ §22: UNSTABLE никогда не становится ACTIVE автоматически."""
        reg = registry
        _register_evaluated(reg, "ML-001", expectancy=0.2,
                            oos_expectancy=0.1, walk_forward_expectancy=0.09)
        _promote_to_validated(reg, "ML-001")
        reg.set_stress_metrics("ML-001", {
            "fees_x2_expectancy": -0.02, "stable": False,
        })
        ok, why = reg.promote("ML-001", "production")
        assert not ok
        assert "stress" in why
        # Даже с override — stress-гейт жёсткий.
        ok, why = reg.promote("ML-001", "production",
                              evidence={"override_reason": "надеюсь"})
        assert not ok

    def test_invalid_transition(self, registry):
        reg = registry
        model = _make_model()
        reg.register(model, version="ML-001")
        ok, why = reg.promote("ML-001", "production")
        assert not ok
        assert "запрещён" in why


class TestABCompare:
    def test_challenger_wins(self, registry):
        reg = registry
        _register_evaluated(reg, "ML-001", expectancy=0.1)
        _register_evaluated(reg, "ML-002", expectancy=0.25)
        res = reg.ab_compare("ML-001", "ML-002")
        assert res["verdict"] == "challenger_wins"
        assert res["delta"] == pytest.approx(0.15)
        assert res["insufficient_samples"] is False

    def test_insufficient_samples_flag(self, registry):
        reg = registry
        reg.register(_make_model(), version="ML-001")
        reg.set_evaluation("ML-001", sample_size=3, expectancy=0.5)
        reg.register(_make_model(), version="ML-002")
        reg.set_evaluation("ML-002", sample_size=100, expectancy=0.2)
        res = reg.ab_compare("ML-001", "ML-002")
        assert res["insufficient_samples"] is True
        assert res["verdict"] == "base_wins"

    def test_ab_gate_blocks_worse_challenger(self, registry):
        reg = registry
        _register_evaluated(reg, "ML-001", expectancy=0.2,
                            oos_expectancy=0.15, walk_forward_expectancy=0.12)
        _promote_to_validated(reg, "ML-001")
        _promote_to_production(reg, "ML-001")
        # Challenger хуже production.
        _register_evaluated(reg, "ML-002", expectancy=0.1,
                            oos_expectancy=0.08, walk_forward_expectancy=0.07)
        _promote_to_validated(reg, "ML-002")
        reg.set_stress_metrics("ML-002", {"stable": True})
        ok, why = reg.promote("ML-002", "production")
        assert not ok
        assert "A/B" in why
        # С осознанным override — допускается.
        ok, why = reg.promote("ML-002", "production",
                              evidence={"override_reason": "новый режим рынка"})
        assert ok, why

    def test_ab_gate_allows_better_challenger(self, registry):
        reg = registry
        _register_evaluated(reg, "ML-001", expectancy=0.1,
                            oos_expectancy=0.08, walk_forward_expectancy=0.07)
        _promote_to_validated(reg, "ML-001")
        _promote_to_production(reg, "ML-001")
        _register_evaluated(reg, "ML-002", expectancy=0.3,
                            oos_expectancy=0.2, walk_forward_expectancy=0.18)
        _promote_to_validated(reg, "ML-002")
        reg.set_stress_metrics("ML-002", {"stable": True})
        ok, why = reg.promote("ML-002", "production")
        assert ok, why
        assert reg.get_production_model().version == "ML-002"
        # Старая production не удалена — demoted, файл на месте.
        old = reg.get_model("ML-001")
        assert old.status == "deprecated"
        assert Path(old.model_path).exists()


class TestRollback:
    def _two_models_in_production(self, reg):
        _register_evaluated(reg, "ML-001", expectancy=0.2,
                            oos_expectancy=0.15, walk_forward_expectancy=0.12)
        _promote_to_validated(reg, "ML-001")
        _promote_to_production(reg, "ML-001")
        _register_evaluated(reg, "ML-002", expectancy=0.3,
                            oos_expectancy=0.2, walk_forward_expectancy=0.18)
        _promote_to_validated(reg, "ML-002")
        reg.set_stress_metrics("ML-002", {"stable": True})
        _promote_to_production(reg, "ML-002")

    def test_rollback_restores_previous(self, registry):
        reg = registry
        self._two_models_in_production(reg)
        ok, previous = reg.rollback("ML-001", reason="ML-002 деградирует")
        assert ok
        assert previous == "ML-002"
        assert reg.get_production_model().version == "ML-001"
        assert reg.get_model("ML-001").status == "production"
        # ML-002 не удалён — demoted с причиной.
        m2 = reg.get_model("ML-002")
        assert m2.status == "deprecated"
        assert "rollback" in m2.status_log[-1]["reason"]
        assert Path(m2.model_path).exists()  # файл сохранён

    def test_rollback_to_deleted_model_blocked(self, registry):
        reg = registry
        self._two_models_in_production(reg)
        assert reg.delete_model("ML-001", reason="мусор") is True
        ok, why = reg.rollback("ML-001")
        assert not ok
        assert "удалённую" in why

    def test_rollback_to_current_blocked(self, registry):
        reg = registry
        self._two_models_in_production(reg)
        ok, why = reg.rollback("ML-002")
        assert not ok
        assert "уже production" in why

    def test_rollback_unknown_version(self, registry):
        ok, why = registry.rollback("ML-999")
        assert not ok
        assert "not found" in why


class TestPersistenceAndSoftDelete:
    def test_roundtrip_with_history(self, tmp_path):
        reg = ModelRegistry(registry_dir=str(tmp_path / "reg"))
        _register_evaluated(reg, "ML-001", expectancy=0.2,
                            oos_expectancy=0.15, walk_forward_expectancy=0.12)
        _promote_to_validated(reg, "ML-001")
        _promote_to_production(reg, "ML-001")
        _register_evaluated(reg, "ML-002", expectancy=0.3,
                            oos_expectancy=0.2, walk_forward_expectancy=0.18)
        _promote_to_validated(reg, "ML-002")
        reg.set_stress_metrics("ML-002", {"stable": True})
        _promote_to_production(reg, "ML-002")
        reg.rollback("ML-001", reason="test")

        reloaded = ModelRegistry(registry_dir=str(tmp_path / "reg"))
        assert reloaded.get_production_model().version == "ML-001"
        m1 = reloaded.get_model("ML-001")
        assert m1.oos_expectancy == pytest.approx(0.15)
        # validated, production, «заменена», rollback -> production.
        assert [e["status"] for e in m1.status_log] == [
            "validated", "production", "deprecated", "production"
        ]
        m2 = reloaded.get_model("ML-002")
        assert m2.status == "deprecated"
        assert m2.stress_metrics.get("stable") is True

    def test_soft_delete_keeps_file(self, registry):
        reg = registry
        model = _make_model()
        reg.register(model, version="ML-001")
        path = reg.get_model("ML-001").model_path
        assert reg.delete_model("ML-001", reason="тест") is True
        assert reg.get_model("ML-001").status == "deprecated"
        assert Path(path).exists()  # TZ §18: файлы не удаляются


class TestLiveWiring:
    def test_engine_loads_production_model(self, tmp_path, monkeypatch):
        """Пайплайн живого движка получает production-модель из registry."""
        from astra_bot.decision.strategy_stats import StrategyStatsStore
        from astra_bot.decision.trading_engine import (
            TradingEngine,
            TradingEngineConfig,
        )
        from tests.integration.test_meta_strategy_execution import (
            FeedStub,
            gen_candles,
            make_pipeline,
        )

        reg_dir = str(tmp_path / "reg")
        reg = ModelRegistry(registry_dir=reg_dir)
        _register_evaluated(reg, "ML-001", expectancy=0.2,
                            oos_expectancy=0.15, walk_forward_expectancy=0.12)
        _promote_to_validated(reg, "ML-001")
        _promote_to_production(reg, "ML-001")

        monkeypatch.setattr(
            "astra_bot.ml.model_registry.get_registry", lambda: reg
        )
        store = StrategyStatsStore(tmp_path / "stats.json")
        feed = FeedStub(gen_candles())
        pipeline = make_pipeline(tmp_path, store)
        assert pipeline.model is None
        cfg = TradingEngineConfig(
            symbols=("BTC-USDT",),
            timeframes=("5m",),
            bars_per_tf={"5m": 250},
            state_path=str(tmp_path / "pos.json"),
            trades_path=str(tmp_path / "trades.jsonl"),
            stats_path=str(tmp_path / "stats.json"),
            no_trade_observations_path=str(tmp_path / "obs.jsonl"),
            no_trade_outcomes_path=str(tmp_path / "outcomes.json"),
            hypotheses_path=str(tmp_path / "hyp.json"),
        )
        from decimal import Decimal

        from astra_bot.decision.broker import PaperBroker

        broker = PaperBroker(
            state_path=tmp_path / "pos.json",
            trades_path=tmp_path / "trades.jsonl",
            initial_capital=Decimal("1000"),
            fee_pct=Decimal("0"),
            slippage_pct=Decimal("0"),
        )
        eng = TradingEngine(exchange=feed, pipeline=pipeline, config=cfg,
                            broker=broker)
        assert isinstance(eng.pipeline.model, MLModel)
        assert eng.pipeline.model.is_fitted

    def test_engine_without_production_keeps_none(self, tmp_path, monkeypatch):
        from astra_bot.decision.strategy_stats import StrategyStatsStore
        from astra_bot.decision.trading_engine import (
            TradingEngine,
            TradingEngineConfig,
        )
        from tests.integration.test_meta_strategy_execution import (
            FeedStub,
            gen_candles,
            make_pipeline,
        )

        reg = ModelRegistry(registry_dir=str(tmp_path / "reg"))
        # Регистрация без продвижения — production отсутствует.
        reg.register(_make_model(), version="ML-001")
        monkeypatch.setattr(
            "astra_bot.ml.model_registry.get_registry", lambda: reg
        )
        store = StrategyStatsStore(tmp_path / "stats.json")
        feed = FeedStub(gen_candles())
        pipeline = make_pipeline(tmp_path, store)
        cfg = TradingEngineConfig(
            symbols=("BTC-USDT",),
            timeframes=("5m",),
            bars_per_tf={"5m": 250},
            state_path=str(tmp_path / "pos.json"),
            trades_path=str(tmp_path / "trades.jsonl"),
            stats_path=str(tmp_path / "stats.json"),
            no_trade_observations_path=str(tmp_path / "obs.jsonl"),
            no_trade_outcomes_path=str(tmp_path / "outcomes.json"),
            hypotheses_path=str(tmp_path / "hyp.json"),
        )
        from decimal import Decimal

        from astra_bot.decision.broker import PaperBroker

        broker = PaperBroker(
            state_path=tmp_path / "pos.json",
            trades_path=tmp_path / "trades.jsonl",
            initial_capital=Decimal("1000"),
            fee_pct=Decimal("0"),
            slippage_pct=Decimal("0"),
        )
        eng = TradingEngine(exchange=feed, pipeline=pipeline, config=cfg,
                            broker=broker)
        assert eng.pipeline.model is None
