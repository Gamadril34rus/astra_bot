"""NO_TRADE-наблюдения: запись, дедупликация, обогащение исходом (TZ §12/§13/§30)."""

from __future__ import annotations

import json
import time
from decimal import Decimal

import pytest
from astra_bot.core import models
from astra_bot.ml.no_trade_observations import (
    NoTradeObservation,
    NoTradeObservationLog,
    make_observation_id,
    quick_features,
)


def _obs(
    symbol="BTC-USDT",
    bar_time=1_700_000_000,
    reason_code="LOW_EV",
    strategy="scalp",
    direction="long",
) -> NoTradeObservation:
    return NoTradeObservation(
        id=make_observation_id(symbol, bar_time, reason_code, strategy, direction),
        symbol=symbol,
        bar_time=bar_time,
        timestamp=bar_time * 1000,
        market_regime="RANGE",
        regime_confidence=0.6,
        reason_code=reason_code,
        reasons=["meta_strategy:LOW_EV"],
        candidate={"strategy": strategy, "direction": direction, "ev_r": -0.25},
        features={"close": 100.0},
    )


def _candles(n=40, base=1700000000, step=900, start_price=100.0, drift=0.1):
    out = []
    price = start_price
    for i in range(n):
        o = price
        c = o + drift
        out.append(
            models.Candle(
                exchange="okx",
                symbol="BTC-USDT",
                timeframe="5m",
                open_time=base + i * step,
                open=Decimal(str(o)),
                high=Decimal(str(max(o, c) + 0.2)),
                low=Decimal(str(min(o, c) - 0.2)),
                close=Decimal(str(c)),
                volume=Decimal("10"),
                quote_volume=Decimal("1000"),
            )
        )
        price = c
    return out


class TestRecordingAndDedup:
    def test_add_appends_once(self, tmp_path):
        log = NoTradeObservationLog(
            observations_path=tmp_path / "obs.jsonl",
            outcomes_path=tmp_path / "out.json",
        )
        obs = _obs()
        assert log.add(obs) is True
        assert log.add(obs) is False  # повтор — дубль не создаётся
        lines = (tmp_path / "obs.jsonl").read_text().strip().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["reason_code"] == "LOW_EV"
        assert row["candidate"]["ev_r"] == -0.25

    def test_id_stable_for_same_input(self):
        a = make_observation_id("SOL-USDT", 123, "LOW_EV", "scalp", "long")
        b = make_observation_id("SOL-USDT", 123, "LOW_EV", "scalp", "long")
        assert a == b
        c = make_observation_id("SOL-USDT", 124, "LOW_EV", "scalp", "long")
        assert a != c

    def test_restart_does_not_duplicate(self, tmp_path):
        path = tmp_path / "obs.jsonl"
        log1 = NoTradeObservationLog(
            observations_path=path, outcomes_path=tmp_path / "out.json"
        )
        log1.add(_obs())
        # «Перезапуск» процесса: новый экземпляр читает существующие id.
        log2 = NoTradeObservationLog(
            observations_path=path, outcomes_path=tmp_path / "out.json"
        )
        assert log2.add(_obs()) is False
        assert len(path.read_text().strip().splitlines()) == 1

    def test_different_bar_is_new_observation(self, tmp_path):
        log = NoTradeObservationLog(
            observations_path=tmp_path / "obs.jsonl",
            outcomes_path=tmp_path / "out.json",
        )
        assert log.add(_obs(bar_time=1_700_000_000)) is True
        assert log.add(_obs(bar_time=1_700_000_900)) is True
        assert len((tmp_path / "obs.jsonl").read_text().strip().splitlines()) == 2


class TestEnrichment:
    def test_forward_outcome_horizons(self, tmp_path):
        log = NoTradeObservationLog(
            observations_path=tmp_path / "obs.jsonl",
            outcomes_path=tmp_path / "out.json",
            horizons=(1, 3),
        )
        # Свежие бары: старые timestamps режутся pruning (30 дней).
        base = int(time.time() - 10 * 900)
        candles = _candles(n=10, base=base, drift=0.1)
        # Наблюдение на баре i=2 (bar_time = base + 2*900).
        obs = _obs(bar_time=base + 2 * 900)
        log.add(obs)

        enriched = log.enrich({"BTC-USDT": candles})
        assert len(enriched) == 1
        row = json.loads((tmp_path / "out.json").read_text())["outcomes"][obs.id]
        entry = float(candles[2].close)
        h1 = row["horizons"]["1"]
        h3 = row["horizons"]["3"]
        # future_return = close[i+1]/close[i] - 1 (для h=1).
        assert h1["future_return"] == pytest.approx(
            float(candles[3].close) / entry - 1.0, abs=1e-6
        )
        assert h1["max_up"] >= h1["future_return"] - 1e-9
        assert h1["max_down"] <= 0.0
        # h=3 — через три бара.
        assert h3["future_return"] == pytest.approx(
            float(candles[5].close) / entry - 1.0, abs=1e-6
        )
        # После обогащения наблюдение не в pending.
        assert log.pending() == []

    def test_not_enriched_until_future_exists(self, tmp_path):
        log = NoTradeObservationLog(
            observations_path=tmp_path / "obs.jsonl",
            outcomes_path=tmp_path / "out.json",
            horizons=(3,),
        )
        base = 1_700_000_000
        candles = _candles(n=5, base=base, drift=0.1)
        obs = _obs(bar_time=base + 2 * 900)  # впереди только 2 бара < 3
        log.add(obs)
        assert log.enrich({"BTC-USDT": candles}) == []
        assert log.pending() == [obs]

    def test_bar_not_found_returns_empty(self, tmp_path):
        log = NoTradeObservationLog(
            observations_path=tmp_path / "obs.jsonl",
            outcomes_path=tmp_path / "out.json",
        )
        obs = _obs(bar_time=42)  # такого бара нет в данных
        log.add(obs)
        assert log.enrich({"BTC-USDT": _candles()}) == []


class TestBackfillJsonl:
    """TZ P0-3: результат обогащения записывается обратно в JSONL."""

    def test_enrich_fills_jsonl_result(self, tmp_path):
        """После enrich() JSONL содержит result != null."""
        log = NoTradeObservationLog(
            observations_path=tmp_path / "obs.jsonl",
            outcomes_path=tmp_path / "out.json",
            horizons=(1, 3),
        )
        base = int(time.time() - 10 * 900)
        candles = _candles(n=10, base=base, drift=0.1)
        obs = _obs(bar_time=base + 2 * 900)
        log.add(obs)

        # Before enrich: result is null in JSONL
        lines = (tmp_path / "obs.jsonl").read_text().strip().splitlines()
        row_before = json.loads(lines[0])
        assert row_before["result"] is None

        # Enrich
        enriched = log.enrich({"BTC-USDT": candles})
        assert len(enriched) == 1

        # After enrich: result is filled in JSONL
        lines = (tmp_path / "obs.jsonl").read_text().strip().splitlines()
        row_after = json.loads(lines[0])
        assert row_after["result"] is not None
        assert "1" in row_after["result"]
        assert "3" in row_after["result"]
        assert "future_return" in row_after["result"]["1"]

    def test_backfill_returns_count(self, tmp_path):
        """backfill_jsonl возвращает количество обновлённых записей."""
        log = NoTradeObservationLog(
            observations_path=tmp_path / "obs.jsonl",
            outcomes_path=tmp_path / "out.json",
            horizons=(1,),
        )
        base = int(time.time() - 10 * 900)
        candles = _candles(n=10, base=base, drift=0.1)
        obs = _obs(bar_time=base + 2 * 900)
        log.add(obs)
        log.enrich({"BTC-USDT": candles})
        # Second call to backfill should return 0 (already filled)
        count = log.backfill_jsonl()
        assert count == 0


class TestQuickFeatures:
    def test_returns_state_without_future(self):
        candles = _candles(n=30, drift=0.1)
        feats = quick_features(candles)
        assert feats["close"] == pytest.approx(float(candles[-1].close), abs=1e-6)
        assert feats["atr25_pct"] > 0
        assert feats["volume_ratio"] == pytest.approx(1.0, abs=1e-3)

    def test_too_few_candles(self):
        assert quick_features(_candles(n=10)) == {}
