# Research: Funding Extreme Fade (SOLUSDT 1h)

**Статус:** только research. Live Zeus, риск, HALT, workflows — **не менялись**.

## Где свечи в репо

| Место | Содержимое |
|-------|------------|
| Ветка `research/positioning-data` → `data/bingx_usdm/` | Рабочий архив BingX: `*_30m.csv`, `*_1h.csv`, `funding_*.csv`, OI |
| `master` → `data/bingx_usdm/` | Почти пусто (`.gitkeep`) — свечи **не** на master |
| Скрипт архива | `scripts/archive_bingx_usdm_candles.py` |

Символы: **BTC, ETH, SOL, HYPE, INJ** (30m + 1h + funding).

## Идея

**Funding Extreme Fade**

1. Z-score funding (~21 день).
2. `fund_z > +2.5` и RSI(14) > 70 → short.
3. `fund_z < -2.5` и RSI(14) < 30 → long.
4. Сигнал на close, вход на open следующего бара.
5. Стоп 1.2×ATR(14), тейк 1.8R, max hold 24h.
6. Риск 1% equity; fee 4 bps + slip 2 bps на сторону.

## Результаты (архив positioning-data, SOLUSDT 1h)

Капитал модели: 10 000 USDT.

| Окно | Сделки | PF | Winrate | Net | Return | Max DD |
|------|--------|-----|---------|-----|--------|--------|
| Full | 15 | **1.38** | 46.7% | +334 | +3.3% | 5.2% |
| 1-я половина | 9 | 1.28 | 44.4% | +151 | +1.5% | 5.2% |
| 2-я половина | 6 | 1.56 | 50.0% | +180 | +1.8% | 3.2% |

### 4 folds

| Fold | Период (прибл.) | N | PF | Net |
|------|-----------------|---|-----|-----|
| 1 | 2025-04 → 2025-09 | 4 | 4.60 | +407 |
| 2 | 2025-09 → 2026-01 | 5 | **0.41** | **-246** |
| 3 | 2026-01 → 2026-06 | 5 | 1.04 | +14 |
| 4 | 2026-06 → 2026-11 | 1 | — | +166 |

**Вывод:** полный период и обе половины плюс, но **мало сделок** и **fold 2 убыточен** — в prod не брать.

### Те же параметры на других символах

| Symbol | N | PF | Net |
|--------|---|-----|-----|
| SOLUSDT | 15 | 1.38 | +334 |
| ETHUSDT | 8 | 0.56 | -234 |
| BTCUSDT | 1 | — | +66 |

На ETH правило **не** переносится.

## Что сканировалось и не прошло устойчивость

EMA pullback+volume, Donchian+ATR, RSI-2 trend, BB squeeze, большая часть funding/RSI-сетки на 30m/1h BTC/ETH/SOL.

## Как прогнать

```bash
git fetch origin research/positioning-data
git checkout origin/research/positioning-data -- data/bingx_usdm

python scripts/research/funding_extreme_fade.py \
  --data-dir data/bingx_usdm \
  --symbol SOLUSDT \
  --timeframe 1h
```

## Next (только по слову owner)

1. Paper/shadow только SOL.
2. Больше истории + запрет подкрутки после теста.
3. Режимный фильтр перед ETH.
4. Включение в Zeus — только явное разрешение.
