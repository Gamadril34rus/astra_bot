# ASTRA BOT — Расчёт прибыли и обучение без депозита

## 1. Какие расчёты прибыли есть в системе

### 1.1. Скальперская позиция (paper engine)

Для каждой открытой позиции используется формула:

```
unrealized_pnl(long)  = (current_price - entry_price) * quantity
unrealized_pnl(short) = (entry_price - current_price) * quantity

pnl         = unrealized_pnl - fees
pnl_pct     = unrealized_pnl / (entry_price * quantity) * 100
```

При закрытии позиции:

```
realized_pnl        += trade.pnl
usdt_balance        += trade.pnl          # теперь баланс реально меняется
total_pnl           += trade.pnl
daily_pnl / weekly_pnl += trade.pnl
equity              = usdt_balance + Σ unrealized_pnl(open positions)
```

> До фикса `usdt_balance` не менялся при закрытии, и итоговая
> прибыльность сделок не влияла на капитал — это было исправлено.

### 1.2. Риск-движок

```
current_drawdown = (high_water_mark - current_equity) / high_water_mark * 100
risk_multiplier  # подбирается по таблице drawdown_adaptation (0 → 1.0,
                 # 3% → 0.75, 5% → 0.5, 8% → 0.0)
allowed_risk     = equity * risk_per_trade * risk_multiplier
```

PnL считается в quote-валюте (для BTC/USDT — в USDT), после закрытия
обновляется `current_equity`, `high_water_mark`, `daily_pnl` и
`weekly_pnl`. Движок хранит сделки за скользящие 24 часа и 7 дней и
очищает их в `_cleanup_old_trades`.

### 1.3. Бэктестер

```
net_profit     = Σ realized_pnl - Σ fees - Σ slippage
gross_profit   = Σ положительных pnl
gross_loss     = |Σ отрицательных pnl|
profit_factor  = gross_profit / gross_loss
win_rate       = wins / total_trades * 100
return_pct     = (final_equity - initial_capital) / initial_capital * 100
max_drawdown   = max((hwm - equity) / hwm)
sharpe         = mean(daily_returns) / std(daily_returns) * √252
```

Equity в бэктестере вычисляется как `realized_equity + Σ unrealized`, а
не накоплением одного и того же unrealized на каждом тике (эта ошибка
также была исправлена).

### 1.4. Метрики ML

Обученная модель ML предсказывает вероятность прибыльной сделки. В
отчёты и Prometheus уходят:

- `accuracy`, `precision`, `recall`, `f1`, `roc_auc`;
- `profit_factor`, `expectancy` (ожидаемая доходность на сделку);
- `max_drawdown_pct`, `sharpe_ratio`;
- кривая капитала и количество примеров обучения.

## 2. Обучение на годовалой истории без депозита

Реализован модуль `astra_bot/ml/historical_training.py` и CLI-скрипт
`scripts/train_historical.py`.

Пайплайн:

1. **Загрузка истории.** `fetch_historical_candles` ходит в OKX
   `/api/v5/market/history-candles` с курсором `before` и собирает
   `lookback_days * 24 * 60 / timeframe_minutes` свечей. По умолчанию
   это год (365 дней) часовых свечей. Публичный эндпоинт не требует
   API-ключа и депозита.
2. **Walk-forward разметка.** Для каждой точки входа из未来 4 баров
   определяется, сработал бы TP (+1.5%) раньше SL (−1%) или наоборот.
   Так получаем бинарную метку «сделка прибыльна / убыточна» без
   совершения реальных ордеров.
3. **Признаки.** `FeaturePipeline.generate_features` считает 39
   признаков (доходности, объёмы, индикаторы, режим рынка) и отдаёт
   их модели.
4. **Обучение.** `ModelTrainer.train` обучает LightGBM/XGBoost/
   RandomForest и сохраняет артефакт в `models/ML-YYYYMMDD-HHMMSS.pkl`,
   опционально регистрируя в `ModelRegistry`.

### Использование

```bash
python scripts/train_historical.py \
    --symbol BTC/USDT \
    --timeframe 1h \
    --days 365 \
    --model lightgbm
```

Или через веб-эндпоинт:

```bash
curl -XPOST 'http://localhost:8000/train?days=365&timeframe=1h&symbol=BTC/USDT'
```

Депозит для обучения **не нужен** — берутся публичные исторические
данные биржи. Реальная торговля не включается.

## 3. Telegram: русское меню и выбор счёта

Бот полностью локализован. Главное меню вызывается командой `/start`:

```
📊 Статус   📈 Отчёт
📍 Позиции 🛡️ Риск
🏥 Здоровье ⚙️ Счёт
❓ Помощь
```

Раздел «⚙️ Счёт» показывает текущий режим и инлайн-кнопки:

- **Демо-счёт** — режим по умолчанию, депозит не нужен;
- **Реальный счёт** — доступен только администраторам, требует
  двойного подтверждения кнопкой «🔒 Подтвердить реальную торговлю».

До подтверждения реальная торговля заблокирована
(`real_trading_confirmed=False`), а Telegram-команды `/pause` и `/resume`
доступны только администраторам из `admin_user_ids`.

Команды `/status`, `/report`, `/positions`, `/risk`, `/health`
дублируют пункты меню и выводят значения капитала, PnL, просадки,
win-rate, риск-режима и числа позиций в рублёвом формате.
