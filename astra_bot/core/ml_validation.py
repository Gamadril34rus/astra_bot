"""Chronological train/test split and OOS metrics (TZ P1.7).

Financial series must not be shuffled. The test fold is always the
most recent ``test_size`` fraction of rows. Random split is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


class LeakageError(ValueError):
    """Raised when a split would leak future information into train."""


@dataclass(frozen=True)
class ChronoSplit:
    X_train: np.ndarray
    X_test: np.ndarray
    y_train: np.ndarray
    y_test: np.ndarray
    cut: int


def assert_chronological(timestamps: np.ndarray | list[Any] | None) -> None:
    """Fail if timestamps are not non-decreasing."""
    if timestamps is None:
        return
    arr = np.asarray(timestamps)
    if arr.size < 2:
        return
    if np.any(arr[1:] < arr[:-1]):
        raise LeakageError("timestamps are not chronological (future leaked into past)")


def chronological_split(
    features: np.ndarray,
    labels: np.ndarray,
    *,
    test_size: float = 0.2,
    timestamps: np.ndarray | list[Any] | None = None,
    shuffle: bool = False,
) -> ChronoSplit:
    """Split so that test = last ``test_size`` rows (future)."""
    if shuffle:
        raise LeakageError("shuffle=True is forbidden for financial ML splits")
    if not 0.0 < test_size < 1.0:
        raise ValueError("test_size must be in (0, 1)")
    assert_chronological(timestamps)
    n = len(labels)
    if n == 0:
        raise ValueError("empty dataset")
    n_test = int(n * test_size)
    if n_test < 1:
        n_test = 1
    if n_test >= n:
        n_test = n - 1
    cut = n - n_test
    return ChronoSplit(
        X_train=features[:cut],
        X_test=features[cut:],
        y_train=labels[:cut],
        y_test=labels[cut:],
        cut=cut,
    )


def oos_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """Accuracy / precision / recall on the future fold only."""
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    n = max(len(y_true), 1)
    acc = float(np.mean(y_true == y_pred)) if len(y_true) else 0.0
    tp = float(np.sum((y_pred == 1) & (y_true == 1)))
    fp = float(np.sum((y_pred == 1) & (y_true == 0)))
    fn = float(np.sum((y_pred == 0) & (y_true == 1)))
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {
        "n": float(len(y_true)),
        "accuracy": acc,
        "precision": precision,
        "recall": recall,
        "positive_rate": float(np.mean(y_true)) if n else 0.0,
    }
