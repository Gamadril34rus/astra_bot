"""Тесты дообучения weekly-модели по урокам self-play."""

import json

import pytest
from astra_bot.ml.weekly_learner import (
    FEATURE_COLUMNS,
    lessons_to_training_data,
    load_lessons,
    train_weekly,
)


def _make_lesson(pnl: float, feats: dict | None = None) -> dict:
    return {
        "trade_id": "t",
        "symbol": "BTC/USDT",
        "direction": "long",
        "pnl": pnl,
        "outcome": "win" if pnl > 0 else "loss",
        "features": feats or {col: 0.1 for col in FEATURE_COLUMNS},
    }


def test_load_lessons_reads_jsonl(tmp_path):
    path = tmp_path / "lessons.jsonl"
    path.write_text(
        json.dumps(_make_lesson(1.0)) + "\n"
        + json.dumps(_make_lesson(-1.0)) + "\n"
    )
    rows = load_lessons(path)
    assert len(rows) == 2
    assert rows[0]["pnl"] == 1.0


def test_lessons_to_training_data_shape():
    lessons = [_make_lesson(1.0), _make_lesson(-1.0)]
    ds = lessons_to_training_data(lessons)
    assert ds.n_samples == 2
    assert ds.features.shape == (2, len(FEATURE_COLUMNS))
    assert set(ds.labels.tolist()) == {0, 1}


def test_train_weekly_skips_when_not_enough_samples(tmp_path):
    lessons = tmp_path / "lessons.jsonl"
    lessons.write_text(json.dumps(_make_lesson(1.0)) + "\n")
    out = tmp_path / "model.pkl"
    result = train_weekly(
        lessons_path=lessons, model_path=out, min_samples=50
    )
    assert result.trained is False
    assert not out.exists()


lightgbm = pytest.importorskip("lightgbm")


def test_train_weekly_trains_and_saves_model(tmp_path):
    import random

    random.seed(7)
    rows = []
    for i in range(300):
        # Простой разделимый сигнал: высокий rsi -> win.
        feats = {col: random.random() for col in FEATURE_COLUMNS}
        feats["rsi"] = 80.0 if i % 2 == 0 else 20.0
        pnl = 1.0 if i % 2 == 0 else -1.0
        rows.append(_make_lesson(pnl, feats))
    lessons = tmp_path / "lessons.jsonl"
    lessons.write_text("\n".join(json.dumps(r) for r in rows))
    out = tmp_path / "current.pkl"

    result = train_weekly(
        lessons_path=lessons,
        model_path=out,
        min_samples=50,
        model_type="lightgbm",
    )
    assert result.trained is True
    assert out.exists()
    assert result.n_samples == 300
    assert result.accuracy > 0.6
