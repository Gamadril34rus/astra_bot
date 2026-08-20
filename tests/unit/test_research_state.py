"""Regression tests for resumable research state and derived memory."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from astra_bot.core.models import Candle
from astra_bot.ml.market_memory import MarketMemory
from astra_bot.ml.research_engine import research_history_v2
from scripts.pretrain_5y_enhanced import _month_has_complete_research, _research_is_complete


def _daily_candles(count: int = 420) -> list[Candle]:
    start = datetime(2020, 1, 1, tzinfo=UTC)
    candles = []
    for index in range(count):
        price = Decimal("100") + Decimal(index) / Decimal("10")
        candles.append(
            Candle(
                exchange="okx",
                symbol="BTC/USDT",
                timeframe="1d",
                open_time=int((start + timedelta(days=index)).timestamp() * 1000),
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price + Decimal("0.2"),
                volume=Decimal("1000") + index,
                quote_volume=Decimal("100000"),
            )
        )
    return candles


def test_research_uses_context_but_emits_only_target_window(tmp_path):
    candles = _daily_candles()
    target_start = candles[230].open_time
    target_end = candles[260].open_time
    output = tmp_path / "observations.jsonl"

    stats = research_history_v2(
        {"BTC/USDT": candles},
        output=output,
        hypotheses_output=tmp_path / "hypotheses.json",
        sample_every=1,
        min_samples=2,
        observation_start_ms=target_start,
        observation_end_ms=target_end,
    )

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert stats["observations"] == 30
    assert stats["validation_observations"] > 0
    assert all(target_start <= row["timestamp"] < target_end for row in rows)
    assert {row["phase"] for row in rows} == {"discovery", "validation"}


def test_market_memory_cumulative_import_is_idempotent_and_bounded(tmp_path):
    source = tmp_path / "research.jsonl"
    rows = []
    for _index in range(2100):
        rows.append(
            json.dumps(
                {
                    "record_type": "market_research",
                    "timeframe": "1h",
                    "market_regime": "trend",
                    "events": ["breakout_up"],
                    "forward": {"1h": {"return": 0.01, "max_up": 0.02, "max_down": -0.01}},
                }
            )
        )
    source.write_text("\n".join(rows), encoding="utf-8")
    memory = MarketMemory(tmp_path / "memory.json")

    memory.import_research(source)
    memory.import_research(source)

    record = memory.data["research"]["breakout_up|1h|trend"]
    assert record["observations"] == 2100
    assert len(record["returns"]["1h"]) == 2000
    assert len(record["max_up"]["1h"]) == 2000
    assert len(record["max_down"]["1h"]) == 2000


def test_completed_month_requires_every_timeframe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    models = tmp_path / "models"
    models.mkdir()
    for timeframe in ("1h", "4h"):
        (models / f"research_observations_2021-08_{timeframe}.jsonl").write_text("{}\n")

    assert not _month_has_complete_research("2021-08")
    assert not _research_is_complete(
        {
            "1h": {"observations": 10, "validation_observations": 3},
            "4h": {"observations": 0, "validation_observations": 0},
        }
    )
