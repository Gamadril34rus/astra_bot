# Slice watch — 2026-09-25 (collect, no judgment)

Источник: `models/zeus_paper_trades.jsonl` + `models/zeus_paper_positions.json` @ master ~25.09 19:00 UTC.

---

## 1. Cohort LINK / BNB / ENA (открыты утром 24.09, channel)

Все **три** закрыты (не две). Итог в ledger:

| Symbol | Dir | Strategy | Closed (UTC) | exit | **R** | stop_pct | costs_r | pnl |
|--------|-----|----------|--------------|------|------:|---------:|--------:|----:|
| **ENA-USDT** | long | channel_boundary_4h | 24.09 **09:32** | stop_loss | **−1.206** | **1.57%** | 0.191 | −0.88 |
| **BNB-USDT** | short | channel_boundary_4h | 24.09 **18:31** | stop_loss | **−0.065** | **4.62%** | 0.065 | −0.15 |
| **LINK-USDT** | short | channel_boundary_4h | 24.09 **18:31** | stop_loss | **−0.046** | **9.20%** | 0.033 | −0.12 |

ids: `c5af13fb…` ENA · `56d427dd…` BNB · `d1da8bc5…` LINK.

**Наблюдение (не вердикт):** ENA с stop_pct 1.57% → costs_r 0.19 и R ≈ −1.2 на стопе; LINK/BNB с более широким стопом → costs_r 0.03–0.07 и малый |R| на стопе. Согласуется с единой гипотезой издержек (`costs_r ≈ 0.3%/stop_pct`), без заявления о skill.

Включить в **Zeus vs Main / channel-only** на 05.10 как закрытые channel-сделки окна.

---

## 2. WIF long — живой кейс «узкий стоп канала»

| Поле | Значение |
|------|----------|
| Источник | журнал (Arena/owner), ~**14:54 UTC 25.09** |
| Setup | long от **нижней границы канала** |
| stop_pct | **0.79%** |
| прогнозный costs_r | **~0.38R** (круг 0.3% / 0.79%) |
| В `zeus_paper_positions.json` @ master | **нет** |
| В `zeus_paper_trades.jsonl` | **нет** (ещё не закрыт / state commit без позиции) |

**Действие:** при закрытии — строка в channel-таблицу среза:

`WIF | long | channel | stop_pct=0.79% | costs_r=… | R=… | exit=…`

как **живой пример** гипотезы узкого стопа. До закрытия — только watch, без force.

Открытая позиция на master сейчас: **BNB-USDT short wedge_retest_4h** (другой id, не cohort channel) — не путать с закрытым channel-BNB.

---

## 3. Календарь (без изменений)

| Дата | Что |
|------|-----|
| **30.09** | Тень гейтов |
| **05.10** | Пакет + драфт (Zeus vs Main постратегийно + wedge-only + channel examples incl. WIF if closed) |
| **06–07.10** | Срез владельца |

Live / тумблеры не трогать.
