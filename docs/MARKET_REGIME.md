# Market Regime 2.0 (Этап A2)

МТЗ §10–14: режим рынка — это **вектор из трёх ортогональных осей**, а не
одна метка. Legacy-классификация (`MarketRegime`) сохранена полностью; оси
строятся поверх неё в `astra_bot/decision/regime_axes.py`.

## Оси

| Ось | Значения | Источник сигнала |
|---|---|---|
| `trend` | `STRONG` / `WEAK` / `RANGE` / `TRANSITION` | EMA-выровненность + ADX (пороги 23/40); breakout или «ADX высокий, структура не выровнена» → `TRANSITION` |
| `volatility` | `VERY_LOW` / `LOW` / `NORMAL` / `HIGH` / `EXTREME` | ATR% от цены; границы 0.5 / 1.5 / 5 / 10 — согласованы с legacy-порогами PANIC/HIGH_VOL/LOW_VOL |
| `liquidity` | `THIN` / `NORMAL` / `DEEP` / `STRESSED` | спред и глубина top-20 стакана; приоритет STRESSED > THIN > DEEP; нет стакана — `NORMAL` (fail-soft для ключа, жёсткие гейты — у OrderBookEngine/LiquidityEngine) |

## Композитный ключ

`axes_key()` = `T:<trend>/V:<volatility>/L:<liquidity>` (например
`T:STRONG/V:NORMAL/L:DEEP`). Символ `|` не используется — он разделитель
ключей в `strategy_stats`.

## Кросс-маркет / relative strength

Из `ctx.global_market` (ключи `btc_change_pct_24h`, `eth_change_pct_24h`,
`sol_change_pct_24h`, `symbol_change_pct_24h`, `btc_regime`) считается
`CrossMarketContext`: `relative_strength_pct` (инструмент минус BTC) и
`rs_bucket` (OUTPERFORM/NEUTRAL/UNDERPERFORM, порог ±1 п.п.), флаг
`majors_risk_off`. Кросс-часть идёт в **diagnostics/features**, но НЕ в
ключ бакета статистики — иначе выборки распылятся.

## Миграция ключей статистики (обратная совместимость)

`StrategyStatsStore` (детали в `META_STRATEGY.md`):

- **запись**: при наличии `regime_axes` сделка пишется в три бакета —
  `strategy|<axes_key>|tf`, legacy `strategy|<regime>|tf` и `ANY`;
- **чтение**: `axes`-бакет → legacy-бакет (если выборка axes пуста —
  например, в старых файлах `models/strategy_stats.json` её ещё нет) →
  `ANY` → prior;
- старые файлы читаются без изменений; даунгрейд бота не теряет данные
  (legacy-бакеты продолжали пополняться).

`MetaStrategy.evaluate_candidate/select` принимают опциональный
`regime_axes`; вызовы без него ведут себя как раньше.

## Где подключено

- `RegimeEngine.classify(..., orderbook=, current_price=, cross_market=)` —
  необязательные параметры; `RegimeReport.axes` и `to_dict()["axes_key"]`;
- `pipeline.decide` передаёт стакан/цену/global_market в classify и axes-ключ
  в `meta.select`;
- `trading_engine` пишет `regime_axes` в позицию (`PaperBroker.open_position`),
  он кочует в `ClosedTrade.regime_axes` и затем в `stats_store.record`;
- позиции до A2 (без поля) загружаются с `regime_axes=""` — путь legacy.

## Границы

Оси — описание, не торговое решение: риск-гейты (PANIC/HIGH_VOL блокировки,
гейты стакана) не ослаблены и остались на своих местах. LLM-слой и события
(A4) полагаются на `to_dict()` отчёта режима.
