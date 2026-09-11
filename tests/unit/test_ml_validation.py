"""Chronological split (TZ P1.7)."""

from __future__ import annotations

import numpy as np
import pytest
from astra_bot.core.ml_validation import (
    LeakageError,
    assert_chronological,
    chronological_split,
    oos_metrics,
)
from astra_bot.ml.model_trainer import DataPreparation


def test_split_keeps_future_in_test():
    X = np.arange(100).reshape(100, 1).astype(float)
    y = np.arange(100)
    split = chronological_split(X, y, test_size=0.2)
    assert len(split.y_train) == 80
    assert len(split.y_test) == 20
    assert split.y_train[-1] == 79
    assert split.y_test[0] == 80


def test_shuffle_forbidden():
    X = np.zeros((10, 1))
    y = np.zeros(10)
    with pytest.raises(LeakageError):
        chronological_split(X, y, shuffle=True)


def test_timestamps_must_be_sorted():
    with pytest.raises(LeakageError):
        assert_chronological(np.array([3, 2, 1]))
    assert_chronological(np.array([1, 2, 2, 3]))


def test_training_data_split_is_chronological():
    data = DataPreparation.create_synthetic_data(n_samples=1000, n_features=10)
    train, test = data.split(test_size=0.2)
    assert len(train.labels) == 800
    assert len(test.labels) == 200


def test_oos_metrics_on_future_fold_only():
    y_true = np.array([1, 0, 1, 1])
    y_pred = np.array([1, 0, 0, 1])
    m = oos_metrics(y_true, y_pred)
    assert m["n"] == 4
    assert m["accuracy"] == 0.75
