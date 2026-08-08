# ASTRA BOT — Trading Specification (Этап B)

**Версия:** 1.0  
**Дата:** 2026-08-08

---

## 1. MOMENTUM STRATEGY

### 1.1 Описание

Trend-following стратегия, которая следует за-established трендом.

### 1.2 Входные данные

- OHLCV свечи (1m, 5m, 15m, 1h, 4h, 1d)
- Объёмы
- Текущий orderbook (опционально)

### 1.3 Индикаторы

| Индикатор | Параметр | Назначение |
|-----------|----------|------------|
| EMA | 20 | Краткосрочный тренд |
| EMA | 50 | Среднесрочный тренд |
| EMA | 200 | Долгосрочный тренд |
| ATR | 14 | Волатильность для стопов |
| RSI | 14 | Фильтр перекупленности/перепроданности |
| Volume SMA | 20 | Подтверждение объёмом |

### 1.4 Условия входа LONG

```
ALL of:
1. EMA20 > EMA50 > EMA200  (тренд вверх)
2. Цена > EMA20            (цена выше краткосрочной EMA)
3. Volume > 1.5 × SMA20    (подтверждение объёмом)
4. RSI < 70                (не перекуплен)
5. ATR > 0                 (волатильность есть)
6. Regime compatible        (BULL_TREND, BREAKOUT, LOW_VOLATILITY)
```

### 1.5 Условия входа SHORT

```
ALL of:
1. EMA20 < EMA50 < EMA200  (тренд вниз)
2. Цена < EMA20            (цена ниже краткосрочной EMA)
3. Volume > 1.5 × SMA20    (подтверждение объёмом)
4. RSI > 30                (не перепродан)
5. ATR > 0
6. Regime compatible        (BEAR_TREND, BREAKOUT)
```

### 1.6 Стоп-лосс

```
SL = Entry ± (ATR × 1.5)

Для LONG:  SL = Entry - ATR × 1.5
Для SHORT: SL = Entry + ATR × 1.5
```

### 1.7 Тейк-профит

```
TP1 = Entry + Risk × 1.0  (1R) — 50% позиции
TP2 = Entry + Risk × 2.0  (2R) — 30% позиции  
TP3 = Entry + Risk × 3.0  (3R) — 20% позиции

Или trailing stop при достижении 2R
```

### 1.8 Размер позиции

```
Position Size = (Capital × Risk%) / Stop Distance
```

Где Risk% = 0.4% (конфигурируется)

### 1.9 Риски и failure modes

| Риск | Mitigation |
|------|------------|
| Ложный пробой | Volume confirmation + EMA alignment |
| Trending market после входа | Trailing stop, частичный выход |
| Волатильность | ATR-based stop, volatility filter |
| Низкая ликвидность | Spread check, min notional check |
| Крипто-корреляция | Portfolio risk limits |

### 1.10 Параметры (configurable)

```yaml
momentum:
  enabled: true
  weight: 1.0
  
  ema_short: 20
  ema_medium: 50
  ema_long: 200
  
  atr_period: 14
  atr_stop_multiplier: 1.5
  
  volume_ratio_threshold: 1.5
  
  min_risk_reward: 1.5
  max_risk_per_trade: 0.005
  
  require_volume_confirmation: true
  require_trend_alignment: true
  
  tp_levels: [1.0, 2.0, 3.0]
  
  lookback_period: 200
```

---

## 2. MEAN REVERSION STRATEGY

### 2.1 Описание

Стратегия возвращения цены к среднему значению в диапазоне.

### 2.2 Входные данные

- OHLCV свечи
- Bollinger Bands
- RSI
- С 탈수рованный volume

### 2.3 Индикаторы

| Индикатор | Параметр | Назначение |
|-----------|----------|------------|
| Bollinger Bands | period=20, std=2.0 | Диапазон |
| RSI | period=14 | Перекупленность |
| Z-Score | period=20 | Отклонение от среднего |
| ATR | 14 | Волатильность |

### 2.4 Условия входа LONG

```
ALL of:
1. Price <= Lower BB  OR  Z-Score < -2.0
2. RSI < 30              ( oversold)
3. Regime = RANGE        (или LOW_VOLATILITY)
4. ATR > min_atr         (достаточная волатильность)
5. ATR < max_atr         (не слишком высокая волатильность)
```

### 2.5 Условия входа SHORT

```
ALL of:
1. Price >= Upper BB  OR  Z-Score > 2.0
2. RSI > 70             (overbought)
3. Regime = RANGE       (или LOW_VOLATILITY)
4. ATR > min_atr
5. ATR < max_atr
```

### 2.6 Стоп-лосс

```
SL = Entry ± (Entry × 2%)

Или за пределами Bollinger Bands + buffer
```

### 2.7 Тейк-профит

```
TP1 = Средняя линия BB (50% позиции)
TP2 = Противоположная BB граница (остаток)

Или при Z-Score < 0.5 (для LONG)
```

### 2.8 Параметры

```yaml
mean_reversion:
  enabled: true
  weight: 1.0
  
  bb_period: 20
  bb_std_dev: 2.0
  
  rsi_period: 14
  rsi_oversold: 30
  rsi_overbought: 70
  
  zscore_threshold: 2.0
  
  min_atr_percent: 0.5
  max_atr_percent: 5.0
  
  sma_period: 50
  
  require_bb_touch: true
  require_rsi_confirm: true
```

### 2.9 Риски

| Риск | Mitigation |
|------|------------|
|Trend breakout | Режим RANGE только, выход при regime change |
| Поддельное touch BB | RSI confirmation, volume filter |
| Сильная trending рынок | Стратегия выключена в тренде |

---

## 3. ADAPTIVE GRID STRATEGY

### 3.1 Описание

Сетевая стратегия для диапазона с автоматической адаптацией к волатильности.

### 3.2 ⚠️ КРИТИЧЕСКИЕ ОГРАНИЧЕНИЯ

- **НЕ Martingale** — запрещено
- **НЕ Averaging Down** — запрещено
- ТОЛЬКО в RANGE режиме
- Автоматически отключается при BREAKOUT/PANIC

### 3.3 Логика сетки

```
Центр сетки: SMA(20) или текущая цена
Шаг сетки: ATR × 1.0 (адаптивно)

Уровни:
  LONG:  Mid - 1×step, Mid - 2×step, ... Mid - N×step
  SHORT: Mid + 1×step, Mid + 2×step, ... Mid + N×step

Максимум позиций: 5 (всего)
Максимум на уровень: 1
```

### 3.4 Входы

```
LONG:  Цена касается или проходит ниже уровня сетки
SHORT: Цена касается или проходит выше уровня сетки

Минимум: 1 уровень от центра
Максимум: 5 уровней от центра
```

### 3.5 Выходы

```
TP: Возврат к центру сетки (или +2% от цены входа)
SL:  Цена выходит за пределы сетки + 5%

Если цена выходит за пределы всей сетки:
- Закрыть все позиции
- Отключить стратегию
- Ждать regime change
```

### 3.6 Параметры

```yaml
adaptive_grid:
  enabled: false  # По умолчанию выключена
  
  grid_levels: 5
  grid_spacing_percent: 1.0
  use_atr_spacing: true
  atr_multiplier: 1.0
  
  max_positions_per_grid: 3
  max_total_grid_positions: 10
  
  min_volume_ratio: 1.0
  max_spread_percent: 0.5
  
  martingale_enabled: false  # ЗАПРЕЩЕНО
  averaging_down_enabled: false  # ЗАПРЕЩЕНО
  
  take_profit_percent: 2.0
  stop_loss_percent: 5.0
  
  adapt_to_volatility: true
  volatility_lookback: 20
```

### 3.7 Риски

| Риск | Mitigation |
|------|------------|
| Breakout диапазона | Срабатывает circuit breaker, полная остановка |
| Сильный тренд | Только в RANGE, regime filter |
| Бесконечное усреднение | Жёсткий SL для всей сетки |
| Martingale temptation | Программно запрещено |

---

## 4. REGIME DETECTOR SPECIFICATION

### 4.1 Режимы

| Режим | Описание | Критерии |
|-------|----------|----------|
| BULL_TREND | Восходящий тренд | EMA20 > EMA50 > EMA200, ADX > 25 |
| BEAR_TREND | Нисходящий тренд | EMA20 < EMA50 < EMA200, ADX > 25 |
| RANGE | Боковик | Слабый тренд, узкие BB, RSI в середине |
| BREAKOUT | Разрыв диапазона | Volume spike + пробой BB |
| HIGH_VOLATILITY | Высокая волатильность | ATR/Price > 3% |
| LOW_VOLATILITY | Низкая волатильность | ATR/Price < 0.5% |
| PANIC | Паника/крах | Резкое падение + volume spike + high volatility |
| UNKNOWN | Не удалось определить | Недостаточно данных |

### 4.2 Индикаторы

- EMA 20/50/200
- ADX (или аналог trend strength)
- ATR/Price %
- Bollinger Bandwidth
- RSI
- Volume ratio
- Price structure (higher highs/lows)

### 4.3 Совместимость стратегий

| Режим | Momentum | Mean Reversion | Grid | Arbitrage |
|-------|----------|----------------|------|-----------|
| BULL_TREND | ON | REDUCED | OFF | ON |
| BEAR_TREND | ON | REDUCED | OFF | ON |
| RANGE | REDUCED | ON | ON | ON |
| BREAKOUT | ON | OFF | OFF | REDUCED |
| HIGH_VOLATILITY | REDUCED | OFF | OFF | OFF |
| LOW_VOLATILITY | ON | ON | REDUCED | ON |
| PANIC | OFF | OFF | OFF | OFF |
| UNKNOWN | OFF | OFF | OFF | ON |

---

## 5. RISK ENGINE SPECIFICATION

### 5.1 Параметры

```yaml
risk:
  risk_per_trade: 0.004  # 0.4%
  daily_loss_limit: 0.02  # 2%
  weekly_loss_limit: 0.04  # 4%
  soft_drawdown: 0.05  # 5%
  hard_drawdown: 0.08  # 8%
  emergency_drawdown: 0.10  # 10%
  max_exposure_pct: 0.30  # 30%
  max_open_positions: 5
```

### 5.2 Drawdown Adaptation

| Просадка | Множитель риска |
|----------|-----------------|
| 0-3% | 1.0 (нормальный) |
| 3-5% | 0.75 (сниженный) |
| 5-8% | 0.5 (оборонительный) |
| 8%+ | 0.0 (стоп) |

### 5.3 Расчёт позиции

```
Risk Amount = Capital × Risk% × Drawdown Multiplier
Position Size = Risk Amount / Stop Distance

If Position Size × Price < Min Notional:
    REJECT: "Номинальная стоимость ниже минимума"
    
If Position Size < Min Quantity:
    REJECT: "Количество ниже минимума"
    
If Open Positions >= Max Positions:
    REJECT: "Достигнут лимит позиций"
```

---

## 6. SIGNAL SCORING SPECIFICATION

### 6.1 Confidence Calculation

```
Base Confidence = 0.5

+ EMA Alignment:        +0.1-0.2
+ Volume Confirmation:  +0.1-0.15
+ RSI in zone:          +0.05-0.1
+ Trend Strength:       +0.05-0.1
+ OB Implication:       +0.05-0.1

Confidence Range: 0.4 - 0.95
```

### 6.2 Expected Value

```
EV = P(win) × Avg Win R - P(loss) × Avg Loss R

If EV < 0:
    REJECT signal
```

---

## 7. STRATEGY KILL SWITCH

### 7.1 Условия срабатывания

| Условие | Действие |
|---------|----------|
| Profit Factor < 1.0 за 20 сделок | Kill switch |
| Win Rate < 40% за 30 сделок | Warning + reduced size |
| Max Drawdown exceeded | Full stop |
| Consecutive losses > 5 | Pause + review |

### 7.2 Восстановление

- После kill switch: требуется ручное подтверждение
- После warning: автоматическое восстановление при улучшении метрик

---

*Документ дополняется по мере разработки.*
