# Model Registry: версии, A/B, stress, rollback

Статус: расширено и подключено к live paper-конттуру (2026-08-28).
Реализует TZ §18–§23 (на базе существующего `astra_bot/ml/model_registry.py`).

## Хранилище

- `models/registry/registry.json` — реестр версий (атомарная запись);
- `models/registry/ML-YYYYMMDD-NNN.pkl` — артефакт модели (pickle).
  Старые артефакты **никогда не удаляются** (нужны для rollback);
  «удаление» — мягкое (статус `deprecated`).
- `status_log[]` у каждой версии — полная история переходов с причинами
  и временными метками.

## Цепочка продвижения (TZ §18)

```text
development → validated → production (= ACTIVE)
                     ↘ deprecated (замена/удаление, rollback-возврат разрешён)
```

| Переход | Гейт |
|---|---|
| development → validated | `sample_size ≥ min_samples (20)` + метрики |
| validated → production | **OOS expectancy > 0** + **walk-forward expectancy > 0** + **stress stable** + A/B: не хуже текущей production (иначе `override_reason` — осознанное решение, пишется в историю) |

**UNSTABLE никогда не становится ACTIVE автоматически** (TZ §22):
`stress_metrics.stable is not True` → продвижение заблокировано даже
с override. Stress = fees×2, slippage×2/×3, Monte Carlo — записывается
`set_stress_metrics()`.

## A/B (TZ §23)

`ab_compare(base, challenger)` — честное сравнение:
`expectancy`, `delta`, sample sizes обоих, флаг `insufficient_samples`,
verdict `challenger_wins / base_wins / tie`. При продвижении challenger'а
хуже production гейт требует `override_reason`.

## Rollback (TZ §18)

```python
registry.rollback("ML-20260828-001", reason="degradation в live")
```

- текущая production → `deprecated` (причина в status_log),
  target → `production`; артефакты обеих версий остаются на диске;
- rollback на версию, **удалённую** пользователем (`delete_model`),
  запрещён; на «снятую при замене» — разрешён;
- повторный rollback (A → B → A) работает по той же механике.

## Live-подключение

`TradingEngine.__init__`: если у пайплайна нет модели, движок
подгружает **production-модель из registry** (`MLModel.load`) — только
она; без production или при сбое загрузки пайплайн работает как раньше
(`ml_probability = None`), live-контур не рвётся. Модель в
`pipeline.model` — тот же объект, что используется в `_ml_probability`
(гейт ML-вероятности в DecisionPipeline).

## Тесты

`tests/unit/test_model_registry_phase4.py` — гейты sample size / OOS /
walk-forward / stress (UNSTABLE не ACTIVE), A/B verdict + флаг малой
выборки, A/B-гейт при продвижении (override), rollback (восстановление,
защита от удалённых, уже-production, неизвестные), персистентность с
полной историей, мягкое удаление (файл на месте), live-подключение
движка (production → MLModel в pipeline; без production → None).
