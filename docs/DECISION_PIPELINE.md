# Decision pipeline

Полная цепочка принятия решения реализована в `astra_bot/decision/`.
Каждый движок — отдельный тестируемый модуль.

```
MarketContext
     ↓
RegimeEngine         → NO_TRADE при PANIC/HIGH_VOL
     ↓
NewsEngine           → NO_TRADE при score ≥ 75
     ↓
TechnicalEngine      → trend/momentum/volatility/volume
     ↓
StructureEngine      → HH/HL, S/R, breakout/fakeout
     ↓
Стратегии            → список SignalCandidate
     ↓
ML-модель            → P(win), отсечка 0.60
     ↓
CorrelationEngine    → блок альт-лонгов при BTC-PANIC
     ↓
EVEngine             → edge ≥ min_expected_edge_pct
     ↓
LiquidityEngine      → cost < edge
     ↓
SignalScorer         → итоговый балл
     ↓
Risk/Position size   → Risk Engine, Kelly-free фикс. %
     ↓
LONG / SHORT / NO_TRADE
```

## Главное правило

*Ни один индикатор не открывает сделку сам по себе.* RSI, EMA, OBV,
стакан, новости, он-чейн и деривативы — это только компоненты скоринга.
Финальное решение принимает пайплайн, и на любом этапе может вернуть
`NO_TRADE` с явной причиной.

## Режимы рынка

`RegimeEngine.classify()` возвращает один из:

```
STRONG_BULL_TREND / WEAK_BULL_TREND
STRONG_BEAR_TREND / WEAK_BEAR_TREND
RANGE / BREAKOUT
HIGH_VOLATILITY / LOW_VOLATILITY
PANIC / UNKNOWN
```

## Пример использования

```python
from decimal import Decimal
from astra_bot.decision import DecisionPipeline, DecisionConfig, MarketContext
from astra_bot.strategies import MomentumStrategy, MeanReversionStrategy

pipe = DecisionPipeline(
    DecisionConfig(),
    strategies=[MomentumStrategy(), MeanReversionStrategy()],
    model=ml_model,  # optional, sklearn-совместимый
)

ctx = MarketContext(
    symbol="BTC/USDT",
    current_price=Decimal("65000"),
    candles={"4h": h4, "1h": h1, "15m": m15},
    orderbook=book,
    news_score=10,
    global_market={"btc_regime": "WEAK_BULL_TREND"},
)

decision = pipe.decide(ctx)
if decision.action != "NO_TRADE":
    print(decision.candidate.to_dict())
else:
    print("пропускаем:", decision.reasons)
```

## Тесты

```bash
pytest tests/unit/test_decision_pipeline.py -v
```

Покрыты: детекция тренда и паники, мультитаймфрейм, блокировки
стаканом/BTC-режимом, EV-расчёт и end-to-end проход пайплайна.

## Что пока осталось заглушками

* Реальные новостные источники (`NewsEngine.assess`)
* Он-чейн метрики (`OnChainEngine`)
* Funding/OI/ликвидации (`DerivativesEngine`)
* Реальный стакан OKX в реальном времени

Они уже имеют интерфейсы и возвращают безопасные дефолты. Это
означает: они **не блокируют** торговлю, пока вы не подключите
данные, но и не подталгивают к сделке сами.
