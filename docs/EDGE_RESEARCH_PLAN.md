# ASTRA Edge Research Plan

ASTRA должна искать не «сигнал для сделки», а устойчивые источники преимущества. Ссылки на известных трейдеров используются как гипотезы, а не как готовые правила.

## 1. Группы подходов

### Quant / Renaissance-style
- cross-sectional momentum and reversal;
- time-series momentum;
- volatility-normalized returns;
- mean reversion;
- lead/lag между активами и площадками;
- статистические аномалии;
- execution cost and slippage;
- ensemble моделей вместо одного предиктора.

Medallion исторически демонстрировал исключительную доходность, но публично неизвестно, какие именно модели использовались. Поэтому ASTRA не должна имитировать «секретную стратегию», а должна исследовать класс количественных сигналов. Публичные оценки Medallion указывают на около 39% годовых после комиссий в 1988–2018, при этом источники и методология оценок различаются.

### Macro / Soros-style
Исследовать:
- инфляция, ставки, DXY, доходности облигаций;
- risk-on/risk-off;
- liquidity shocks;
- крупные новости;
- расхождение ожиданий и факта;
- рефлексивные циклы: движение цены → изменение поведения участников → дальнейшее движение.

### Macro + tactical / Druckenmiller-style
- определить dominant driver текущего режима;
- измерять breadth и confirmation;
- искать асимметрию reward/risk;
- быстро снижать уверенность при invalidation;
- не держать позицию только потому, что первоначальная гипотеза была красивой.

### Price action / Livermore-style
Исследовать статистически:
- trend persistence;
- breakout и failed breakout;
- pivot / swing levels;
- volume confirmation;
- retest;
- acceleration/deceleration;
- признаки распределения и накопления.

### Risk-first / Tudor Jones-style
- volatility regime;
- ATR / realized volatility;
- gap/shock risk;
- maximum adverse excursion;
- stop distance;
- position sizing;
- корреляция открытых позиций;
- portfolio heat.

## 2. Long / Short engine

Long и short не должны быть зеркальными boolean-сигналами. Для каждого направления считать отдельные вероятности и распределения исходов:

- P(up | state);
- P(down | state);
- expected return;
- expected adverse excursion;
- expected favorable excursion;
- probability of continuation;
- probability of reversal;
- confidence interval;
- transaction-cost adjusted expectancy.

Решение допускается только если ожидаемое преимущество сохраняется после комиссии, spread, slippage и funding.

## 3. Market microstructure

Изучать отдельно:
- bid/ask spread;
- order-book imbalance;
- trade/taker imbalance;
- volume shocks;
- open interest;
- funding;
- liquidations;
- basis;
- cross-exchange price divergence;
- lead/lag;
- latency-sensitive effects.

Высокочастотное преимущество не считать доступным ASTRA автоматически: если эффект исчезает после реалистичной задержки и издержек, он помечается как unusable.

## 4. Event studies

Для каждого существенного события строить окна до/после события: 1m, 5m, 15m, 1h, 4h, 1d. Исследовать не только цену, но объём, волатильность, OI, funding, корреляции и поведение BTC/ETH/альтов.

## 5. Regime engine

Классифицировать хотя бы:
- strong trend;
- weak trend;
- range;
- breakout;
- high volatility;
- low volatility;
- panic/liquidation;
- news shock;
- recovery;
- correlation breakdown.

Один и тот же сигнал должен проверяться отдельно по режимам.

## 6. Research discipline

Каждая гипотеза обязана проходить:
1. discovery sample;
2. validation sample;
3. walk-forward;
4. out-of-sample;
5. cost/slippage stress;
6. sensitivity analysis;
7. multiple-testing / data-snooping check.

ASTRA должна сохранять и подтверждённые, и опровергнутые гипотезы.

## 7. Главный принцип

Не искать способ «никогда не уходить в минус». В реальном рынке это невозможно гарантировать. Цель системы — положительное математическое ожидание после издержек, контролируемый drawdown и способность прекращать торговлю, когда обнаруженное преимущество исчезает.
