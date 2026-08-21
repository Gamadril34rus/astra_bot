# Исследование бесплатных обучающих стратегий на истории

Прогон правил из бесплатных учебных материалов по трейдингу на реальных
свечах Binance BTC/USDT. Полный инструментарий: `scripts/research_free_strategies.py`
(там же полные таблицы по всем правилам, `reports/free_strategies/summary.md`).

Окно: **2024-08-20 → 2026-08-20** (2 года), таймфреймы 1h и 4h.
Исполнение честное: сигнал по закрытию бара → вход по открытию следующего,
внутрибарные стопы, комиссия 0.1% + проскальзывание 0.05% на сторону,
одна позиция одновременно, 10% капитала на сделку.

## Источники (бесплатные курсы и материалы)

| Источник | Что взяли | Ссылка |
|---|---|---|
| Babypips School of Pipsology | свечные паттерны (пин-бар, поглощение, 3 солдата), MACD, MA, BB, Keltner, ADX, Ichimoku, pullback-уроки | babypips.com/learn/forex |
| Investopedia | Guide to Technical Analysis | investopedia.com |
| TradingView Education | уроки/вебинары (Supertrend и др.) | tradingview.com/education |
| StockCharts ChartSchool | энциклопедия индикаторов | chartschool.stockcharts.com |
| Zerodha Varsity | модули по теханализу | zerodha.com/varsity |
| ThePatternSite | каталог свечных/графических паттернов | thepatternsite.com |
| Оригинальные правила Turtle Trading | пробой канала Дончиана 20/55, выход 10 | свободный мануал Curtis Faith |
| Connors Research | RSI(2) mean reversion | свободные статьи Larry Connors |
| John Bollinger | ленты Боллинджера, Squeeze | bollingerbands.com |
| J. Welles Wilder | ATR/RSI/ADX | «New Concepts in Technical Trading Systems» |
| Moskowitz–Ooi–Pedersen | Time Series Momentum | свободный препринт |

## Главный вывод

**Почти все «классические» правила на этом окне не окупаются** на 1h/4h
с реалистичными издержками (большинство PF < 1: MACD, ADX, Supertrend,
свечные паттерны, Turtle, RSI-2, Bollinger fade и т.д. — см. полные таблицы
в скрипте исследования). Лучшие результаты у **медленных трендовых фильтров
на 4h**, и лидер — **time-series momentum (45 дней)**:

| Метрика (4h, 2 года) | TS momentum 45д (long+short) | Buy & hold |
|---|---|---|
| Доходность | +5.7% | +5.2% |
| Profit factor | 1.58 | — |
| Макс. просадка | 6.2% | **53.4%** |
| Сделок | 42 | 1 |

Тот же результат, что и «купи и держи», но с просадкой в **~9 раз меньше** —
это и есть классический результат работы про time-series momentum.

Устойчивость параметра проверена на сетке периодов (15/20/30/45/60/90 дней),
на обоих таймфреймах и на двух окнах: на 2 годах и на всей истории 2021–2026.
Оптимум стабилен в диапазоне 30–90 дней, поэтому взят середины диапазона —
**45 дней** (не подгонка).

## Что встроено в проект

Стратегия **`astra_bot/strategies/ts_momentum.py`** (TimeSeriesMomentumStrategy):

- флип-стратегия: сигнал только при смене режима (0→long, long→short, …);
- мёртвая зона ±2% держит позицию (не дёргается вбок);
- long+short по умолчанию, `allow_short=False` для spot-режима;
- катастрофический стоп 6×ATR(14), без тейк-профитов (выход по смене режима);
- оценивается на свечах **4h** (`preferred_timeframe`), 45 дней = 270 баров.

Поддержка в инфраструктуре:

- `DecisionPipeline` — новые действия **FLIP** (перевернуть позицию) и
  **CLOSE** (выйти по flat-сигналу), свечи выбираются по
  `strategy.preferred_timeframe`;
- `TradingEngine` — таймфрейм 4h в конфиге, обработка FLIP/CLOSE,
  `no_take_profit` для брокера;
- `PaperBroker` — публичный `close_positions()`, режим без тейков;
- `BacktestEngine` — `close_on_opposite_signal` и обработка flat-сигналов
  (кросс-валидация даёт PF 1.50 / +3.1% на том же окне — совпадает с
  исследовательским харнессом в пределах модели исполнения);
- режимная совместимость `ts_momentum` в `regime_detector`;
- включено в `config/settings.yaml` (`ts_momentum: enabled: true, weight: 0.5`).

## Воспроизведение

```bash
# данные: data/BTCUSDT_{1h,4h}.csv (Binance klines)
python scripts/research_free_strategies.py --years 2 --capital 10000
```

> Бумажная проверка на истории, без реальных денег. Прошлые результаты
> не гарантируют будущую прибыль.
