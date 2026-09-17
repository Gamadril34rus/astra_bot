# Spec: zeus_wedge_retest_4h (enabled=false)

**Do not enable before slice 26–27.09.**

## Registry

| key | preferred_timeframe | enabled | tier |
|-----|---------------------|---------|------|
| `zeus_wedge_retest_4h` | `4h` | **false** | research / audit |

## Logic (урок 8)

1. Lookback 24 бара: сходящиеся границы (rising/falling wedge proxy).
2. Close за верхней/нижней границей (ложный выход).
3. Ретест границы снаружи (≤ max_bars_outside).
4. Close обратно **внутри** формации → сигнал в сторону возврата.
5. Stop за экстремумом ложного пробоя + buffer; TP = min_rr (pipeline clamp 2.0–2.3R).

## Defaults

```yaml
zeus_wedge_retest_4h:
  enabled: false
  preferred_timeframe: "4h"
  lookback: 24
  min_touches: 3
  max_width_pct: 0.08
  min_width_pct: 0.008
  breakout_buffer_pct: 0.001
  retest_tolerance_pct: 0.003
  stop_buffer_pct: 0.002
  min_rr: 1.5
  max_bars_outside: 6
```

## Tests

`tests/test_zeus_wedge_retest.py` — disabled guard + synthetic false-upside-break → SHORT.

## Pipeline costs

Production BingX ~0.30% RT, probation ×0.25, EV-gate — без изменений в этом PR.
