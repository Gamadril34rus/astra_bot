# Прибыльная стратегия (Parabolic SAR) — заметка для внедрения

**Дата фиксации:** 2026-11-17  
**Обновлено:** 2026-11-18 — сверка с видео; 15m baseline в отдельной ветке исследования  
**Статус:** исследование / кандидат в бот (ещё НЕ включена в live ASTRA)  
**Биржа:** BingX USDT-M Perpetual Futures

> Цифры — из исторических прогонов. Не гарантия будущей доходности.

---

## 0. Воспроизводимость (обязательно читать)

Раньше в чате были цифры без полного артефакта (даты, PF, CSV, код). Это исправлено.

| Что | Где |
|-----|-----|
| Движок SAR + портфель | [`docs/research/sar_repro/sar_backtest_engine.py`](sar_repro/) (запуск локально; исходник в research-сессии) |
| README пакета | [`docs/research/sar_repro/README.md`](sar_repro/README.md) |
| Snapshot метрик | [`docs/research/sar_repro/summary.json`](sar_repro/summary.json) |
| Сделки / equity CSV | генерируются командой `python sar_backtest_engine.py --start 5000` |

**Cost model (как demo-бот):** fee **0.05%** + slip **0.1%** на сторону. Funding = 0 в скрипте.  
Старые прогоны со slip **0.03%** завышали доходность — не использовать.

### Исторический snapshot (НЕ использовать как подтверждённый результат)

**ВНИМАНИЕ:** значения ниже из `verification_sample.json` (1975 сделок, PF 1.70, +3918%) относятся к прежней версии движка. `AUDIT_V2.md` документирует look-ahead, вход по close и некорректную метрику PF; эти результаты официально инвалидированы. Текущий `summary.json` содержит другой прогон v4: 2048 сделок, PF_usd около 0.745 и отрицательный результат. Это тоже предварительный 1h-прогон, а не воспроизведение видео.

Окно свечей (UTC), старый 1h snapshot:

- BTC/ETH 1h: **2025-11-08 → 2026-11-18** (8992 бара)  
- SOL 1h: **2025-12-20 → 2026-11-18** (7993 бара)  

Сделок (принятых): **1975** | WR **41.6%** | **PF 1.70** | avg net **+33.0 bps**  
Старт: **5000**

| Профиль | Final | Return | Max DD |
|---------|-------|--------|--------|
| 1% / cap 3% | **200,898** | +3918% | **−41.4%** |
| 1.25% / 5% | 429,707 | +8494% | −48.9% |
| 1.5% / 6% | 867,442 | +17249% | −55.6% |
| 2% / 8% | **2,976,227** | +59425% | **−66.7%** |
| 3% / 12% | 15,064,539 | +301191% | −87.6% |

> Цифры «~270к / ~5.1 млн» из более ранних сообщений чата относились к другим перезапросам klines и **не** являются каноном. Канон — `summary.json` + повторный запуск engine.

---

## 1. Суть стратегии

### Правила непосредственно из предоставленного видео

На экране TradingView явно выбран XAUUSD, таймфрейм **15м**, Parabolic SAR `0.02 / 0.02 / 0.2`. Текстовая инструкция в самом видео: точки под ценой и закрытая свеча — покупка; стоп на первой точке, тейка нет; на каждой новой свече стоп переносится на новую точку; касание точки — выход из рынка. Для продажи применяется зеркальное положение точек над ценой. Видео не раскрывает формулу размера позиции, плечо и издержки. XAUUSD — демонстрационный инструмент автора, а не целевой рынок ASTRA.

Для ASTRA это означает, что базовый крипто-аналог надо сначала тестировать на **15m**, а не считать прежний 1h прогон точным повторением ролика. 1h оставляем только как отдельный контрольный эксперимент.

- SAR **под** ценой → long после close; **над** → short.  
- Стоп = SAR (трейл каждый бар), без тейка.  
- Сайзинг: risk % equity до стопа. Плечо только потолок.  
- SAR: `0.02 / 0.02 / 0.2`, TF **15m** (как в видео), монеты **BTC+ETH+SOL**, `min_prev=0`, без EMA/vol фильтров.

---

## 2. Издержки

| Статья | На сторону | Round-trip |
|--------|------------|------------|
| Taker | 0.05% | 0.10% |
| Slippage paper | 0.10% | 0.20% |
| Итого | | **~0.30%** |

Источник: `astra_bot/engines/cost_model.py` → `bingx_perps_cost_model`.

---

## 3. Что не использовать ради «улучшения DD»

EMA50, volume filter, BE, time-stop — **режут финальную сумму**. Для max-result не включать.

---

## 4. Профили для разбора

- **A)** risk **1.5%** / sum **6%**  
- **B)** risk **2%** / sum **8%**  
- **C)** risk **3%** / sum **12%**  

Одна логика, три субсчёта/репо — сравнение по факту.

---

## 5. Ограничения

- Не walk-forward в текущем engine (можно добавить).  
- Funding = 0.  
- История ≠ будущее.  
- Не включать в live ASTRA без решения владельца.

---

## 6. Шпаргалка

```
TF 15m (video analogue) | SAR 0.02/0.02/0.2 | BTC ETH SOL
entry: SAR flip + close | exit: SAR touch
risk 1.5% / 2% / 3% | cap 6% / 8% / 12%
fee 0.05% + slip 0.1% per side
reproduce: python sar_backtest_engine.py --start 5000 --interval 15m --limit-total 36000
```

## 2026-11-21 final 15m reproduction result

The fixed video baseline was tested on BingX 15m data over approximately 100 days (9,600 BTC bars, 9,599 ETH and SOL bars, no gaps). The public endpoint did not provide the requested 36,000 bars, so this is not a one-year test.

The replay produced 2,311 trades, mean net -27.85 bps per trade, win rate 21.46%, raw trade PF 0.441, and portfolio PF 0.421 at 1% risk / 3% max open risk. Starting $5,000 ended at $3.24 with -99.947% maximum drawdown. BTC, ETH and SOL all lost; both long and short sides lost.

Price-only mean was +2.59 bps per trade, below the 10 bps round-trip taker fee even before slippage. With 10 bps slippage per side and 5 bps taker fee per side, net mean was -27.85 bps. Funding-adjusted subset (2,046 fully covered trades) was -27.98 bps mean with PF 0.417; funding was negligible and did not rescue the strategy.

Median holding time was 165 minutes; 96.67% of trades were held 30 minutes to 9 hours. Duration fits the intraday target, but expectancy does not.

**Decision: reject the fixed SAR video baseline for BingX USDT-M perpetuals.** Do not tune this discovery period or connect it to Zeus. Full reproducible report: `docs/research/sar_repro/SAR_15M_VIDEO_BASELINE_RESULT_2026-11-21.md`. Workflow run `44910671263`, artifact `14709825927`.
