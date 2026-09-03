"""Research Memory: типизированные записи, idempotency (TZ §14/§30)."""

from __future__ import annotations

import json

import pytest
from astra_bot.ml.research_memory import ResearchMemory, observation_id


@pytest.fixture()
def mem(tmp_path):
    return ResearchMemory(
        observations_path=tmp_path / "observations.jsonl",
        hypotheses_path=tmp_path / "hypotheses.json",
    )


class TestObservationSchema:
    def test_record_has_required_fields(self, mem):
        oid = mem.record_observation(
            source="market_research",
            symbol="BTC-USDT",
            bar_time=1_700_000_000,
            kind="research_event",
            features={"rsi_14": 55.0, "atr_pct": 1.2},
            confidence=0.4,
            sample_size=1,
        )
        assert oid is not None
        row = json.loads(
            mem.observations_path.read_text().strip().splitlines()[0]
        )
        for field in (
            "id", "timestamp", "type", "source", "version", "confidence",
            "sample_size",
        ):
            assert field in row
        assert row["id"] == oid
        assert row["source"] == "market_research"
        assert row["kind"] == "research_event"

    def test_duplicate_same_input_no_duplicate(self, mem):
        kwargs = dict(
            source="market_research",
            symbol="BTC-USDT",
            bar_time=1_700_000_000,
            kind="research_event",
            features={"rsi_14": 55.0},
        )
        first = mem.record_observation(**kwargs)
        second = mem.record_observation(**kwargs)
        assert first is not None
        assert second is None
        lines = mem.observations_path.read_text().strip().splitlines()
        assert len(lines) == 1

    def test_different_features_new_id(self, mem):
        a = mem.record_observation(
            source="s", symbol="BTC-USDT", bar_time=1, kind="k",
            features={"x": 1.0},
        )
        b = mem.record_observation(
            source="s", symbol="BTC-USDT", bar_time=1, kind="k",
            features={"x": 2.0},
        )
        assert a != b

    def test_restart_dedup(self, tmp_path):
        path = tmp_path / "obs.jsonl"
        mem1 = ResearchMemory(observations_path=path)
        mem1.record_observation(
            source="s", symbol="SOL-USDT", bar_time=7, kind="k", features={"a": 1}
        )
        mem2 = ResearchMemory(observations_path=path)
        assert mem2.record_observation(
            source="s", symbol="SOL-USDT", bar_time=7, kind="k", features={"a": 1}
        ) is None
        assert mem2.count() == 1

    def test_observation_id_stable(self):
        a = observation_id("src", "BTC-USDT", 123, "kind", "digest")
        b = observation_id("src", "BTC-USDT", 123, "kind", "digest")
        assert a == b


class TestSeparation:
    """TZ §14: память разделена по типам, не один JSON."""

    def test_hypotheses_store_attached(self, mem, tmp_path):
        hyp_path = tmp_path / "hypotheses.json"
        from astra_bot.ml.hypothesis_engine import HypothesisStore

        assert isinstance(mem.hypotheses, HypothesisStore)
        assert mem.hypotheses.path == hyp_path

    def test_observations_is_jsonl_archive(self, mem):
        mem.record_observation(
            source="s", symbol="BTC-USDT", bar_time=1, kind="k", features={"a": 1}
        )
        assert mem.observations_path.suffix == ".jsonl"
