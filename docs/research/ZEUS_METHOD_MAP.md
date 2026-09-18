# Zeus method map → astra (research-only)

Источник: 9 транскриптов `arena/01a08cde-astra-bot` (1.txt–9.txt) + 8 видео-уроков
(X / Yandex). Live-контур **не** включаем до среза 26–27.09.

## Иерархия (главное сверху)

| Ранг | Правило Зевса | Урок | Что в astra |
|------|---------------|------|-------------|
| 1 | Риск 1–3% на сделку + стоп всегда | 7–8 | Risk engine / sizer (уже) |
| 2 | Пауза 1–2 дня после убытка; no FOMO; не добивать | 8 | cooldown / kill-switch / entry_gates |
| 3 | Partials + стоп в Б/У | 8 | production exit 30/70 + BE |
| 4 | Сначала фаза рынка | 2 | HTF context / regime |
| 5 | Зоны спроса/предложения | 3 | structure / levels research |
| 6 | Канал / клин: вход от границы | 5, 7 | channel logic |
| 7 | Ложный выход из клина → ретест → закрепление внутри → разворот | **8** | **`zeus_wedge_retest_4h`** |
| 8 | FVG / имбаланс | 4, 6 | fair_value_gap |
| 9 | Объём / MA / RSI — фильтр | 9 | indicators filter only |

## Онбординг

```yaml
zeus_wedge_retest_4h:
  enabled: false
  preferred_timeframe: 4h
  lookback: 24
  min_touches: 3
  min_rr: 1.5
```

Путь: shadow → probation ×0.25 → promote только при n≥5 и live PF≥1.
Не поднимать веса 5m. Не смешивать статистику с 5m.

## Track C

Z3/урок 8 близки по логике; deep-history OOS не доказал сильный edge под нашими издержками — пакет для тени и накопления n, не для немедленного риска.

## Риск (копейка за копейкой)

1. Не увеличивать risk% ради частоты.
2. Пауза после серии лоссов (kill-switch).
3. entry_gates — решение на срезе по median<0 при n≥30.
4. Новые семьи только enabled=false → shadow → ×0.25.
5. Universe: BTC/ETH/SOL важнее 35 размытых bucket-ов.
