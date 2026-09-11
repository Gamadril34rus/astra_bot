# Feature schema (P1.6) и chronological ML (P1.7)

Канон: `astra_bot/core/feature_schema.py`.

- `FEATURE_SCHEMA_VERSION = "1.0.0"`
- `CANONICAL_FEATURE_NAMES` — стабильный порядок полей `Features` (без `raw`)
- `MLModel.save/load` пишет/проверяет версию. Чужой версии — `FeatureSchemaError` (fail-closed)

Метрики обучения — только на **будущем** фолде (`astra_bot/core/ml_validation.py`). `TrainingData.split` больше не делает `train_test_split(shuffle/stratify)`. `shuffle=True` — ошибка. CV — `TimeSeriesSplit`.
