"""Leakage-safe chronological trainer for market data."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

import numpy as np
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score

from .model_trainer import (
    LIGHTGBM_AVAILABLE,
    MLModel,
    ModelMetrics,
    TrainingConfig,
    TrainingData,
    _lgb_eval_kwargs,
    lgb,
)


def train_temporal(training_data: TrainingData, config: TrainingConfig | None = None) -> MLModel:
    """Train on the oldest samples and test only on the newest samples.

    The lesson list must already be chronological by entry_time. No random
    shuffle is used, so the validation set represents unseen future market data.
    """
    cfg = config or TrainingConfig(model_type="lightgbm")
    if not LIGHTGBM_AVAILABLE:
        raise ImportError("lightgbm is required for temporal training")
    if training_data.n_samples < 50:
        raise ValueError("Слишком мало наблюдений для temporal training")
    if len(np.unique(training_data.labels)) < 2:
        raise ValueError("В temporal dataset должен присутствовать WIN и LOSS")

    split = max(int(training_data.n_samples * (1.0 - cfg.test_size)), 1)
    split = min(split, training_data.n_samples - 1)
    X_train = training_data.features[:split]
    y_train = training_data.labels[:split]
    X_test = training_data.features[split:]
    y_test = training_data.labels[split:]

    # Нативные параметры LightGBM: min_samples_leaf → min_child_samples,
    # а sklearn-алиасы (min_samples_split/min_samples_leaf) не передаём —
    # иначе LightGBM ругается на неизвестный параметр и на переопределение
    # min_data_in_leaf в каждом прогоне CI.
    model = lgb.LGBMClassifier(
        n_estimators=cfg.n_estimators,
        max_depth=cfg.max_depth,
        learning_rate=cfg.learning_rate,
        min_child_samples=cfg.min_samples_leaf,
        verbose=cfg.verbose,
        random_state=cfg.random_state,
        n_jobs=-1,
    )

    started = time.monotonic()
    fit_kwargs: dict[str, Any] = dict(_lgb_eval_kwargs(X_test, y_test))
    if cfg.early_stopping_rounds > 0:
        fit_kwargs["callbacks"] = [lgb.early_stopping(cfg.early_stopping_rounds, verbose=False)]
    model.fit(X_train, y_train, **fit_kwargs)

    pred = model.predict(X_test)
    proba = model.predict_proba(X_test)[:, 1]
    try:
        auc = float(roc_auc_score(y_test, proba))
    except ValueError:
        auc = 0.0

    metrics = ModelMetrics(
        accuracy=float(accuracy_score(y_test, pred)),
        precision=float(precision_score(y_test, pred, zero_division=0)),
        recall=float(recall_score(y_test, pred, zero_division=0)),
        f1_score=float(f1_score(y_test, pred, zero_division=0)),
        roc_auc=auc,
        feature_importance={
            name: float(value)
            for name, value in zip(training_data.feature_names, model.feature_importances_, strict=False)
        },
        training_time_seconds=time.monotonic() - started,
    )
    return MLModel(
        model=model,
        config=cfg,
        metrics=metrics,
        feature_names=training_data.feature_names,
    )
