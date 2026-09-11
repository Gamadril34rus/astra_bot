# Unified cost model (P1.9)

Источник правды: `astra_bot/engines/cost_model.py`.

Используется PaperBroker, бэктестер (частично — см. `BACKTEST_VS_PAPER_SEMANTICS.md`), ExecutionEngine.

| Поле | Paper BingX perps | Смысл |
|---|---|---|
| `taker_fee_rate` | 0.05% | всегда taker |
| `maker_fee_rate` | 0.02% | бэктест limit |
| `slippage_pct` | 0.1% на сторону | худшая сторона |
| `funding_rate` | живой / дефолт брокера | при закрытии |
| `min_notional` / `tick_size` / `lot_size` | 0 в модели | режет TradingEngine A6 по `Instrument` |
| `reject_penalty` / `rate_limit_penalty` | 0 | резерв, не начисляются автоматически |

Инвариант: каждая сторона сделки ≥ notional × min_fee_rate. Нулевой taker запрещён.
