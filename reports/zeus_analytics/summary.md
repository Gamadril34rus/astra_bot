# ZEUS analytics

Read-only отчёт из репозиторных JSON/JSONL. Генератор не меняет models/ или исходные reports/.
Paper, явно маркированный VST, явно маркированный REAL и live UNKNOWN разделены. UNKNOWN не считается VST.
PF = gross positive pnl / abs(gross negative pnl). Fees показаны отдельно, второй раз из pnl не вычитаются.
Paper partial-close rows объединены по id; PnL, fees и R суммируются по legs в рамках одной сделки.

## 1. Стратегии

| контур | стратегия | PF | winrate, % | средний R | expectancy, USDT/сделка | fees, USDT | период MSK | источник | n сделок |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| paper | channel | 4.45 | 55.79 | 0.837 | 1.6606 | 74.6517 | 2026-09-21 — 2026-10-30 MSK | models/zeus_paper_trades.jsonl | 380 |
| paper | wedge | 0.82 | 40.74 | -0.001 | -0.1481 | 10.0234 | 2026-09-22 — 2026-10-06 MSK | models/zeus_paper_trades.jsonl | 54 |
| paper | multi-top | нет данных | нет данных | нет данных | нет данных | нет данных | нет данных | models/zeus_paper_trades.jsonl | 0 (нет данных) |
| VST | channel | нет данных | нет данных | нет данных | нет данных | нет данных | нет данных | models/zeus_live_trades.jsonl | 0 (нет данных) |
| VST | wedge | нет данных | нет данных | нет данных | нет данных | нет данных | нет данных | models/zeus_live_trades.jsonl | 0 (нет данных) |
| VST | multi-top | нет данных | нет данных | нет данных | нет данных | нет данных | нет данных | models/zeus_live_trades.jsonl | 0 (нет данных) |
| REAL | channel | нет данных | нет данных | нет данных | нет данных | нет данных | нет данных | models/zeus_live_trades.jsonl | 0 (нет данных) |
| REAL | wedge | нет данных | нет данных | нет данных | нет данных | нет данных | нет данных | models/zeus_live_trades.jsonl | 0 (нет данных) |
| REAL | multi-top | нет данных | нет данных | нет данных | нет данных | нет данных | нет данных | models/zeus_live_trades.jsonl | 0 (нет данных) |
| live / UNKNOWN | channel | 0.53 | 23.23 | — | -93.9610 | 5266.4392 | 2026-10-13 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | 99 |
| live / UNKNOWN | wedge | нет данных | нет данных | нет данных | нет данных | нет данных | нет данных | models/zeus_live_trades.jsonl | 0 (нет данных) |
| live / UNKNOWN | multi-top | 1.30 | 35.29 | — | 26.0940 | 584.1273 | 2026-10-30 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | 17 (мало данных) |

Live environment определяется только по явному env/environment/contour/is_vst/is_real в строке. Отсутствие маркировки не восполняется значением workflow по умолчанию.

## 2. Профили сессий

Час открытия сделки в MSK; profile вычисляет та же raw_bucket() из live_session.py. Разбивка: fat/normal/thin/off × будни/выходные × symbol × direction.

### paper — summary

| контур | профиль | день | n сделок | период MSK | источник | n / статус |
| --- | --- | --- | --- | --- | --- | --- |
| paper | fat | будни | 105 | 2026-09-21 — 2026-10-26 MSK | models/zeus_paper_trades.jsonl | 105 |
| paper | fat | выходные | нет данных за период | нет данных | models/zeus_paper_trades.jsonl | нет данных за период |
| paper | normal | будни | 107 | 2026-09-21 — 2026-10-29 MSK | models/zeus_paper_trades.jsonl | 107 |
| paper | normal | выходные | 31 | 2026-10-03 — 2026-10-24 MSK | models/zeus_paper_trades.jsonl | 31 |
| paper | thin | будни | 43 | 2026-09-21 — 2026-10-30 MSK | models/zeus_paper_trades.jsonl | 43 |
| paper | thin | выходные | 32 | 2026-09-27 — 2026-10-11 MSK | models/zeus_paper_trades.jsonl | 32 |
| paper | off | будни | 22 | 2026-10-02 — 2026-10-26 MSK | models/zeus_paper_trades.jsonl | 22 |
| paper | off | выходные | 94 | 2026-09-26 — 2026-10-24 MSK | models/zeus_paper_trades.jsonl | 94 |

### paper — symbol × direction

| контур | профиль | день | символ | направление | n сделок | период MSK | источник | n / статус |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| paper | fat | будни | 1000PEPE-USDT | long | 2 (мало данных) | 2026-10-05 — 2026-10-05 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | fat | будни | 1000PEPE-USDT | short | 1 (мало данных) | 2026-09-30 — 2026-09-30 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | fat | будни | AAVE-USDT | long | 2 (мало данных) | 2026-09-21 — 2026-10-05 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | fat | будни | ADA-USDT | long | 2 (мало данных) | 2026-10-05 — 2026-10-05 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | fat | будни | ADA-USDT | short | 10 (мало данных) | 2026-09-28 — 2026-10-20 MSK | models/zeus_paper_trades.jsonl | 10 (мало данных) |
| paper | fat | будни | APT-USDT | long | 2 (мало данных) | 2026-10-05 — 2026-10-05 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | fat | будни | APT-USDT | short | 3 (мало данных) | 2026-10-09 — 2026-10-26 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | fat | будни | ARB-USDT | short | 2 (мало данных) | 2026-09-28 — 2026-10-14 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | fat | будни | ATOM-USDT | long | 6 (мало данных) | 2026-10-05 — 2026-10-19 MSK | models/zeus_paper_trades.jsonl | 6 (мало данных) |
| paper | fat | будни | ATOM-USDT | short | 3 (мало данных) | 2026-10-08 — 2026-10-15 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | fat | будни | AVAX-USDT | long | 2 (мало данных) | 2026-09-21 — 2026-09-30 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | fat | будни | AVAX-USDT | short | 2 (мало данных) | 2026-09-29 — 2026-10-20 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | fat | будни | BNB-USDT | short | 2 (мало данных) | 2026-10-08 — 2026-10-15 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | fat | будни | BTC-USDT | short | 1 (мало данных) | 2026-10-08 — 2026-10-08 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | fat | будни | DOGE-USDT | long | 1 (мало данных) | 2026-09-23 — 2026-09-23 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | fat | будни | DOGE-USDT | short | 3 (мало данных) | 2026-09-30 — 2026-10-20 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | fat | будни | DOT-USDT | long | 3 (мало данных) | 2026-09-28 — 2026-10-23 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | fat | будни | DOT-USDT | short | 2 (мало данных) | 2026-10-13 — 2026-10-13 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | fat | будни | ENA-USDT | long | 7 (мало данных) | 2026-10-05 — 2026-10-06 MSK | models/zeus_paper_trades.jsonl | 7 (мало данных) |
| paper | fat | будни | ENA-USDT | short | 3 (мало данных) | 2026-10-07 — 2026-10-20 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | fat | будни | HYPE-USDT | short | 1 (мало данных) | 2026-10-20 — 2026-10-20 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | fat | будни | LINK-USDT | long | 1 (мало данных) | 2026-09-30 — 2026-09-30 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | fat | будни | LINK-USDT | short | 3 (мало данных) | 2026-09-21 — 2026-10-08 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | fat | будни | LTC-USDT | long | 2 (мало данных) | 2026-09-28 — 2026-10-05 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | fat | будни | NEAR-USDT | long | 6 (мало данных) | 2026-10-05 — 2026-10-08 MSK | models/zeus_paper_trades.jsonl | 6 (мало данных) |
| paper | fat | будни | OP-USDT | short | 2 (мало данных) | 2026-09-29 — 2026-10-20 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | fat | будни | SUI-USDT | short | 2 (мало данных) | 2026-10-19 — 2026-10-20 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | fat | будни | TAO-USDT | long | 1 (мало данных) | 2026-10-16 — 2026-10-16 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | fat | будни | TAO-USDT | short | 2 (мало данных) | 2026-10-08 — 2026-10-20 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | fat | будни | TIA-USDT | long | 6 (мало данных) | 2026-10-05 — 2026-10-08 MSK | models/zeus_paper_trades.jsonl | 6 (мало данных) |
| paper | fat | будни | TIA-USDT | short | 2 (мало данных) | 2026-10-07 — 2026-10-08 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | fat | будни | UNI-USDT | short | 3 (мало данных) | 2026-10-06 — 2026-10-06 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | fat | будни | WIF-USDT | long | 5 (мало данных) | 2026-10-05 — 2026-10-20 MSK | models/zeus_paper_trades.jsonl | 5 (мало данных) |
| paper | fat | будни | WIF-USDT | short | 4 (мало данных) | 2026-10-12 — 2026-10-16 MSK | models/zeus_paper_trades.jsonl | 4 (мало данных) |
| paper | fat | будни | WLD-USDT | long | 1 (мало данных) | 2026-10-05 — 2026-10-05 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | fat | будни | WLD-USDT | short | 4 (мало данных) | 2026-10-09 — 2026-10-16 MSK | models/zeus_paper_trades.jsonl | 4 (мало данных) |
| paper | fat | будни | XRP-USDT | long | 1 (мало данных) | 2026-09-23 — 2026-09-23 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | будни | 1000PEPE-USDT | long | 1 (мало данных) | 2026-10-14 — 2026-10-14 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | будни | 1000PEPE-USDT | short | 1 (мало данных) | 2026-10-01 — 2026-10-01 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | будни | AAVE-USDT | long | 3 (мало данных) | 2026-09-30 — 2026-10-07 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | normal | будни | ADA-USDT | long | 6 (мало данных) | 2026-10-07 — 2026-10-07 MSK | models/zeus_paper_trades.jsonl | 6 (мало данных) |
| paper | normal | будни | ADA-USDT | short | 7 (мало данных) | 2026-09-29 — 2026-10-15 MSK | models/zeus_paper_trades.jsonl | 7 (мало данных) |
| paper | normal | будни | APT-USDT | long | 5 (мало данных) | 2026-10-06 — 2026-10-07 MSK | models/zeus_paper_trades.jsonl | 5 (мало данных) |
| paper | normal | будни | APT-USDT | short | 1 (мало данных) | 2026-10-01 — 2026-10-01 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | будни | ARB-USDT | short | 2 (мало данных) | 2026-10-14 — 2026-10-28 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | normal | будни | ATOM-USDT | long | 6 (мало данных) | 2026-10-05 — 2026-10-22 MSK | models/zeus_paper_trades.jsonl | 6 (мало данных) |
| paper | normal | будни | ATOM-USDT | short | 12 (мало данных) | 2026-10-08 — 2026-10-23 MSK | models/zeus_paper_trades.jsonl | 12 (мало данных) |
| paper | normal | будни | AVAX-USDT | long | 4 (мало данных) | 2026-09-21 — 2026-10-07 MSK | models/zeus_paper_trades.jsonl | 4 (мало данных) |
| paper | normal | будни | BCH-USDT | short | 1 (мало данных) | 2026-10-02 — 2026-10-02 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | будни | BNB-USDT | short | 2 (мало данных) | 2026-09-24 — 2026-09-24 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | normal | будни | DOT-USDT | short | 2 (мало данных) | 2026-10-16 — 2026-10-20 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | normal | будни | ENA-USDT | long | 5 (мало данных) | 2026-09-24 — 2026-10-28 MSK | models/zeus_paper_trades.jsonl | 5 (мало данных) |
| paper | normal | будни | ETH-USDT | long | 1 (мало данных) | 2026-09-22 — 2026-09-22 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | будни | FIL-USDT | long | 3 (мало данных) | 2026-10-07 — 2026-10-07 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | normal | будни | HYPE-USDT | short | 1 (мало данных) | 2026-09-30 — 2026-09-30 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | будни | INJ-USDT | long | 1 (мало данных) | 2026-10-07 — 2026-10-07 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | будни | INJ-USDT | short | 2 (мало данных) | 2026-09-29 — 2026-10-07 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | normal | будни | LTC-USDT | short | 2 (мало данных) | 2026-10-07 — 2026-10-07 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | normal | будни | NEAR-USDT | long | 6 (мало данных) | 2026-09-23 — 2026-10-08 MSK | models/zeus_paper_trades.jsonl | 6 (мало данных) |
| paper | normal | будни | OP-USDT | long | 1 (мало данных) | 2026-10-06 — 2026-10-06 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | будни | OP-USDT | short | 1 (мало данных) | 2026-10-07 — 2026-10-07 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | будни | SOL-USDT | long | 2 (мало данных) | 2026-09-24 — 2026-10-02 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | normal | будни | SUI-USDT | short | 3 (мало данных) | 2026-10-07 — 2026-10-15 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | normal | будни | TAO-USDT | short | 1 (мало данных) | 2026-09-30 — 2026-09-30 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | будни | TIA-USDT | long | 6 (мало данных) | 2026-09-28 — 2026-10-07 MSK | models/zeus_paper_trades.jsonl | 6 (мало данных) |
| paper | normal | будни | TIA-USDT | short | 4 (мало данных) | 2026-10-07 — 2026-10-08 MSK | models/zeus_paper_trades.jsonl | 4 (мало данных) |
| paper | normal | будни | UNI-USDT | long | 2 (мало данных) | 2026-09-24 — 2026-10-29 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | normal | будни | WIF-USDT | long | 1 (мало данных) | 2026-10-01 — 2026-10-01 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | будни | WIF-USDT | short | 7 (мало данных) | 2026-10-12 — 2026-10-23 MSK | models/zeus_paper_trades.jsonl | 7 (мало данных) |
| paper | normal | будни | WLD-USDT | long | 1 (мало данных) | 2026-10-05 — 2026-10-05 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | будни | WLD-USDT | short | 2 (мало данных) | 2026-09-22 — 2026-10-19 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | normal | будни | XRP-USDT | long | 2 (мало данных) | 2026-09-22 — 2026-10-02 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | normal | выходные | 1000PEPE-USDT | short | 1 (мало данных) | 2026-10-11 — 2026-10-11 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | выходные | ADA-USDT | short | 2 (мало данных) | 2026-10-11 — 2026-10-11 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | normal | выходные | APT-USDT | short | 1 (мало данных) | 2026-10-04 — 2026-10-04 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | выходные | ARB-USDT | short | 1 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | выходные | ATOM-USDT | long | 2 (мало данных) | 2026-10-04 — 2026-10-11 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | normal | выходные | ATOM-USDT | short | 2 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | normal | выходные | AVAX-USDT | short | 1 (мало данных) | 2026-10-11 — 2026-10-11 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | выходные | BCH-USDT | long | 2 (мало данных) | 2026-10-04 — 2026-10-04 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | normal | выходные | DOT-USDT | short | 1 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | выходные | HYPE-USDT | short | 1 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | выходные | LINK-USDT | short | 3 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | normal | выходные | NEAR-USDT | long | 1 (мало данных) | 2026-10-11 — 2026-10-11 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | выходные | SOL-USDT | short | 1 (мало данных) | 2026-10-11 — 2026-10-11 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | выходные | SUI-USDT | short | 2 (мало данных) | 2026-10-11 — 2026-10-11 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | normal | выходные | TIA-USDT | long | 2 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | normal | выходные | UNI-USDT | short | 1 (мало данных) | 2026-10-24 — 2026-10-24 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | normal | выходные | WIF-USDT | short | 3 (мало данных) | 2026-10-11 — 2026-10-11 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | normal | выходные | WLD-USDT | long | 4 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 4 (мало данных) |
| paper | off | будни | 1000PEPE-USDT | short | 2 (мало данных) | 2026-10-15 — 2026-10-26 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | off | будни | ADA-USDT | short | 1 (мало данных) | 2026-10-02 — 2026-10-02 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | off | будни | APT-USDT | long | 1 (мало данных) | 2026-10-07 — 2026-10-07 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | off | будни | APT-USDT | short | 1 (мало данных) | 2026-10-09 — 2026-10-09 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | off | будни | AVAX-USDT | long | 1 (мало данных) | 2026-10-08 — 2026-10-08 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | off | будни | BNB-USDT | long | 1 (мало данных) | 2026-10-02 — 2026-10-02 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | off | будни | ENA-USDT | long | 1 (мало данных) | 2026-10-06 — 2026-10-06 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | off | будни | ENA-USDT | short | 5 (мало данных) | 2026-10-07 — 2026-10-09 MSK | models/zeus_paper_trades.jsonl | 5 (мало данных) |
| paper | off | будни | FIL-USDT | long | 1 (мало данных) | 2026-10-07 — 2026-10-07 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | off | будни | INJ-USDT | short | 2 (мало данных) | 2026-10-09 — 2026-10-09 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | off | будни | TAO-USDT | short | 3 (мало данных) | 2026-10-08 — 2026-10-15 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | off | будни | TIA-USDT | long | 2 (мало данных) | 2026-10-07 — 2026-10-07 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | off | будни | WIF-USDT | long | 1 (мало данных) | 2026-10-05 — 2026-10-05 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | off | выходные | AAVE-USDT | long | 4 (мало данных) | 2026-09-26 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 4 (мало данных) |
| paper | off | выходные | ADA-USDT | long | 1 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | off | выходные | ADA-USDT | short | 2 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | off | выходные | APT-USDT | short | 6 (мало данных) | 2026-10-03 — 2026-10-24 MSK | models/zeus_paper_trades.jsonl | 6 (мало данных) |
| paper | off | выходные | ARB-USDT | short | 2 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | off | выходные | ATOM-USDT | long | 1 (мало данных) | 2026-10-11 — 2026-10-11 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | off | выходные | ATOM-USDT | short | 3 (мало данных) | 2026-10-03 — 2026-10-18 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | off | выходные | BCH-USDT | long | 3 (мало данных) | 2026-10-04 — 2026-10-04 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | off | выходные | BCH-USDT | short | 2 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | off | выходные | BNB-USDT | short | 1 (мало данных) | 2026-09-26 — 2026-09-26 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | off | выходные | DOT-USDT | long | 2 (мало данных) | 2026-09-26 — 2026-10-24 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | off | выходные | DOT-USDT | short | 1 (мало данных) | 2026-10-17 — 2026-10-17 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | off | выходные | ENA-USDT | long | 1 (мало данных) | 2026-10-24 — 2026-10-24 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | off | выходные | ENA-USDT | short | 1 (мало данных) | 2026-10-04 — 2026-10-04 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | off | выходные | ETH-USDT | short | 1 (мало данных) | 2026-09-26 — 2026-09-26 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | off | выходные | FIL-USDT | long | 2 (мало данных) | 2026-10-03 — 2026-10-17 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | off | выходные | FIL-USDT | short | 2 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | off | выходные | HYPE-USDT | short | 5 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 5 (мало данных) |
| paper | off | выходные | INJ-USDT | short | 4 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 4 (мало данных) |
| paper | off | выходные | LINK-USDT | long | 1 (мало данных) | 2026-09-26 — 2026-09-26 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | off | выходные | LINK-USDT | short | 4 (мало данных) | 2026-10-03 — 2026-10-04 MSK | models/zeus_paper_trades.jsonl | 4 (мало данных) |
| paper | off | выходные | LTC-USDT | long | 2 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | off | выходные | NEAR-USDT | short | 7 (мало данных) | 2026-10-03 — 2026-10-04 MSK | models/zeus_paper_trades.jsonl | 7 (мало данных) |
| paper | off | выходные | OP-USDT | short | 2 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | off | выходные | SUI-USDT | short | 4 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 4 (мало данных) |
| paper | off | выходные | TAO-USDT | short | 5 (мало данных) | 2026-10-03 — 2026-10-04 MSK | models/zeus_paper_trades.jsonl | 5 (мало данных) |
| paper | off | выходные | TIA-USDT | long | 3 (мало данных) | 2026-09-26 — 2026-10-04 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | off | выходные | TIA-USDT | short | 3 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | off | выходные | UNI-USDT | short | 3 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | off | выходные | WIF-USDT | long | 10 (мало данных) | 2026-10-03 — 2026-10-04 MSK | models/zeus_paper_trades.jsonl | 10 (мало данных) |
| paper | off | выходные | WIF-USDT | short | 3 (мало данных) | 2026-10-18 — 2026-10-24 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | off | выходные | WLD-USDT | long | 2 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | off | выходные | XRP-USDT | long | 1 (мало данных) | 2026-09-26 — 2026-09-26 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | будни | 1000PEPE-USDT | long | 1 (мало данных) | 2026-10-02 — 2026-10-02 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | будни | ADA-USDT | short | 1 (мало данных) | 2026-10-19 — 2026-10-19 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | будни | APT-USDT | short | 2 (мало данных) | 2026-10-08 — 2026-10-28 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | thin | будни | ARB-USDT | long | 1 (мало данных) | 2026-10-22 — 2026-10-22 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | будни | AVAX-USDT | long | 4 (мало данных) | 2026-10-08 — 2026-10-08 MSK | models/zeus_paper_trades.jsonl | 4 (мало данных) |
| paper | thin | будни | BCH-USDT | long | 1 (мало данных) | 2026-09-28 — 2026-09-28 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | будни | BCH-USDT | short | 1 (мало данных) | 2026-09-21 — 2026-09-21 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | будни | BNB-USDT | long | 2 (мало данных) | 2026-09-22 — 2026-10-30 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | thin | будни | BNB-USDT | short | 1 (мало данных) | 2026-10-02 — 2026-10-02 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | будни | DOGE-USDT | long | 1 (мало данных) | 2026-10-06 — 2026-10-06 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | будни | DOGE-USDT | short | 1 (мало данных) | 2026-10-02 — 2026-10-02 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | будни | DOT-USDT | short | 2 (мало данных) | 2026-10-08 — 2026-10-16 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | thin | будни | ENA-USDT | long | 1 (мало данных) | 2026-10-06 — 2026-10-06 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | будни | ENA-USDT | short | 2 (мало данных) | 2026-09-22 — 2026-10-20 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | thin | будни | INJ-USDT | long | 6 (мало данных) | 2026-10-06 — 2026-10-13 MSK | models/zeus_paper_trades.jsonl | 6 (мало данных) |
| paper | thin | будни | LINK-USDT | short | 2 (мало данных) | 2026-09-24 — 2026-10-19 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | thin | будни | LTC-USDT | short | 1 (мало данных) | 2026-10-01 — 2026-10-01 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | будни | NEAR-USDT | long | 4 (мало данных) | 2026-10-06 — 2026-10-12 MSK | models/zeus_paper_trades.jsonl | 4 (мало данных) |
| paper | thin | будни | OP-USDT | short | 1 (мало данных) | 2026-10-19 — 2026-10-19 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | будни | SOL-USDT | short | 1 (мало данных) | 2026-09-30 — 2026-09-30 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | будни | TIA-USDT | short | 1 (мало данных) | 2026-09-30 — 2026-09-30 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | будни | WIF-USDT | short | 3 (мало данных) | 2026-10-19 — 2026-10-23 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | thin | будни | WLD-USDT | long | 2 (мало данных) | 2026-10-06 — 2026-10-06 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | thin | будни | WLD-USDT | short | 1 (мало данных) | 2026-09-21 — 2026-09-21 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | выходные | APT-USDT | long | 1 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | выходные | ARB-USDT | short | 2 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | thin | выходные | ATOM-USDT | long | 1 (мало данных) | 2026-10-11 — 2026-10-11 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | выходные | ATOM-USDT | short | 1 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | выходные | BCH-USDT | long | 2 (мало данных) | 2026-10-04 — 2026-10-04 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | thin | выходные | DOT-USDT | short | 1 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | выходные | ENA-USDT | short | 3 (мало данных) | 2026-10-03 — 2026-10-04 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | thin | выходные | LINK-USDT | short | 4 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 4 (мало данных) |
| paper | thin | выходные | LTC-USDT | long | 1 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | выходные | NEAR-USDT | short | 1 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | выходные | PENDLE-USDT | long | 1 (мало данных) | 2026-10-04 — 2026-10-04 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |
| paper | thin | выходные | SUI-USDT | short | 2 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | thin | выходные | TIA-USDT | long | 3 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 3 (мало данных) |
| paper | thin | выходные | WIF-USDT | long | 2 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 2 (мало данных) |
| paper | thin | выходные | WLD-USDT | long | 6 (мало данных) | 2026-10-03 — 2026-10-03 MSK | models/zeus_paper_trades.jsonl | 6 (мало данных) |
| paper | thin | выходные | XRP-USDT | short | 1 (мало данных) | 2026-09-27 — 2026-09-27 MSK | models/zeus_paper_trades.jsonl | 1 (мало данных) |

### VST — summary

| контур | профиль | день | n сделок | период MSK | источник | n / статус |
| --- | --- | --- | --- | --- | --- | --- |
| VST | fat | будни | нет данных за период | нет данных | models/zeus_live_trades.jsonl | нет данных за период |
| VST | fat | выходные | нет данных за период | нет данных | models/zeus_live_trades.jsonl | нет данных за период |
| VST | normal | будни | нет данных за период | нет данных | models/zeus_live_trades.jsonl | нет данных за период |
| VST | normal | выходные | нет данных за период | нет данных | models/zeus_live_trades.jsonl | нет данных за период |
| VST | thin | будни | нет данных за период | нет данных | models/zeus_live_trades.jsonl | нет данных за период |
| VST | thin | выходные | нет данных за период | нет данных | models/zeus_live_trades.jsonl | нет данных за период |
| VST | off | будни | нет данных за период | нет данных | models/zeus_live_trades.jsonl | нет данных за период |
| VST | off | выходные | нет данных за период | нет данных | models/zeus_live_trades.jsonl | нет данных за период |

### VST — symbol × direction

| контур | профиль | день | символ | направление | n сделок | период MSK | источник | n / статус |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| нет данных за период | — | — | — | — | — | — | — | — |

### REAL — summary

| контур | профиль | день | n сделок | период MSK | источник | n / статус |
| --- | --- | --- | --- | --- | --- | --- |
| REAL | fat | будни | нет данных за период | нет данных | models/zeus_live_trades.jsonl | нет данных за период |
| REAL | fat | выходные | нет данных за период | нет данных | models/zeus_live_trades.jsonl | нет данных за период |
| REAL | normal | будни | нет данных за период | нет данных | models/zeus_live_trades.jsonl | нет данных за период |
| REAL | normal | выходные | нет данных за период | нет данных | models/zeus_live_trades.jsonl | нет данных за период |
| REAL | thin | будни | нет данных за период | нет данных | models/zeus_live_trades.jsonl | нет данных за период |
| REAL | thin | выходные | нет данных за период | нет данных | models/zeus_live_trades.jsonl | нет данных за период |
| REAL | off | будни | нет данных за период | нет данных | models/zeus_live_trades.jsonl | нет данных за период |
| REAL | off | выходные | нет данных за период | нет данных | models/zeus_live_trades.jsonl | нет данных за период |

### REAL — symbol × direction

| контур | профиль | день | символ | направление | n сделок | период MSK | источник | n / статус |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| нет данных за период | — | — | — | — | — | — | — | — |

### live / UNKNOWN — summary

| контур | профиль | день | n сделок | период MSK | источник | n / статус |
| --- | --- | --- | --- | --- | --- | --- |
| live / UNKNOWN | fat | будни | 73 | 2026-10-13 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | 73 |
| live / UNKNOWN | fat | выходные | нет данных за период | нет данных | models/zeus_live_trades.jsonl | нет данных за период |
| live / UNKNOWN | normal | будни | 28 | 2026-10-13 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | 28 |
| live / UNKNOWN | normal | выходные | 2 (мало данных) | 2026-10-24 — 2026-10-31 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | thin | будни | 5 (мало данных) | 2026-10-13 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | 5 (мало данных) |
| live / UNKNOWN | thin | выходные | 5 (мало данных) | 2026-10-31 — 2026-11-01 MSK | models/zeus_live_trades.jsonl | 5 (мало данных) |
| live / UNKNOWN | off | будни | 7 (мало данных) | 2026-10-13 — 2026-10-30 MSK | models/zeus_live_trades.jsonl | 7 (мало данных) |
| live / UNKNOWN | off | выходные | 3 (мало данных) | 2026-10-31 — 2026-10-31 MSK | models/zeus_live_trades.jsonl | 3 (мало данных) |

### live / UNKNOWN — symbol × direction

| контур | профиль | день | символ | направление | n сделок | период MSK | источник | n / статус |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| live / UNKNOWN | fat | будни | 1000PEPE-USDT | long | 1 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | AAVE-USDT | short | 2 (мало данных) | 2026-10-16 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | fat | будни | ADA-USDT | short | 3 (мало данных) | 2026-10-20 — 2026-10-29 MSK | models/zeus_live_trades.jsonl | 3 (мало данных) |
| live / UNKNOWN | fat | будни | APT-USDT | long | 1 (мало данных) | 2026-10-16 — 2026-10-16 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | APT-USDT | short | 2 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | fat | будни | ARB-USDT | short | 2 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | fat | будни | ATOM-USDT | short | 1 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | AVAX-USDT | short | 4 (мало данных) | 2026-10-22 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 4 (мало данных) |
| live / UNKNOWN | fat | будни | BCH-USDT | long | 1 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | BCH-USDT | short | 1 (мало данных) | 2026-10-13 — 2026-10-13 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | BTC-USDT | long | 2 (мало данных) | 2026-11-05 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | fat | будни | BTC-USDT | short | 2 (мало данных) | 2026-10-29 — 2026-10-29 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | fat | будни | DOGE-USDT | short | 2 (мало данных) | 2026-10-20 — 2026-10-27 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | fat | будни | DOT-USDT | short | 2 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | fat | будни | ETH-USDT | long | 4 (мало данных) | 2026-11-02 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | 4 (мало данных) |
| live / UNKNOWN | fat | будни | ETH-USDT | short | 6 (мало данных) | 2026-10-13 — 2026-11-03 MSK | models/zeus_live_trades.jsonl | 6 (мало данных) |
| live / UNKNOWN | fat | будни | FIL-USDT | short | 1 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | HYPE-USDT | long | 2 (мало данных) | 2026-10-28 — 2026-11-02 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | fat | будни | HYPE-USDT | short | 5 (мало данных) | 2026-11-02 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | 5 (мало данных) |
| live / UNKNOWN | fat | будни | INJ-USDT | long | 1 (мало данных) | 2026-10-16 — 2026-10-16 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | INJ-USDT | short | 2 (мало данных) | 2026-11-03 — 2026-11-03 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | fat | будни | LINK-USDT | short | 1 (мало данных) | 2026-10-20 — 2026-10-20 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | LTC-USDT | short | 3 (мало данных) | 2026-10-16 — 2026-10-22 MSK | models/zeus_live_trades.jsonl | 3 (мало данных) |
| live / UNKNOWN | fat | будни | NEAR-USDT | short | 1 (мало данных) | 2026-10-13 — 2026-10-13 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | OP-USDT | long | 1 (мало данных) | 2026-10-13 — 2026-10-13 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | OP-USDT | short | 1 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | PENDLE-USDT | short | 1 (мало данных) | 2026-10-29 — 2026-10-29 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | SOL-USDT | long | 3 (мало данных) | 2026-10-19 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | 3 (мало данных) |
| live / UNKNOWN | fat | будни | SOL-USDT | short | 7 (мало данных) | 2026-10-27 — 2026-11-03 MSK | models/zeus_live_trades.jsonl | 7 (мало данных) |
| live / UNKNOWN | fat | будни | SUI-USDT | short | 3 (мало данных) | 2026-10-16 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 3 (мало данных) |
| live / UNKNOWN | fat | будни | TAO-USDT | short | 1 (мало данных) | 2026-10-29 — 2026-10-29 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | XRP-USDT | short | 4 (мало данных) | 2026-10-13 — 2026-10-27 MSK | models/zeus_live_trades.jsonl | 4 (мало данных) |
| live / UNKNOWN | normal | будни | ADA-USDT | short | 1 (мало данных) | 2026-10-29 — 2026-10-29 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | AVAX-USDT | short | 3 (мало данных) | 2026-10-14 — 2026-10-29 MSK | models/zeus_live_trades.jsonl | 3 (мало данных) |
| live / UNKNOWN | normal | будни | BNB-USDT | long | 1 (мало данных) | 2026-10-29 — 2026-10-29 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | BTC-USDT | long | 1 (мало данных) | 2026-10-19 — 2026-10-19 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | BTC-USDT | short | 4 (мало данных) | 2026-11-03 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | 4 (мало данных) |
| live / UNKNOWN | normal | будни | DOGE-USDT | short | 1 (мало данных) | 2026-10-13 — 2026-10-13 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | DOT-USDT | long | 1 (мало данных) | 2026-10-23 — 2026-10-23 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | DOT-USDT | short | 3 (мало данных) | 2026-10-16 — 2026-10-19 MSK | models/zeus_live_trades.jsonl | 3 (мало данных) |
| live / UNKNOWN | normal | будни | ETH-USDT | long | 2 (мало данных) | 2026-10-29 — 2026-11-03 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | normal | будни | ETH-USDT | short | 4 (мало данных) | 2026-10-19 — 2026-10-30 MSK | models/zeus_live_trades.jsonl | 4 (мало данных) |
| live / UNKNOWN | normal | будни | INJ-USDT | long | 1 (мало данных) | 2026-11-04 — 2026-11-04 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | LINK-USDT | short | 1 (мало данных) | 2026-10-23 — 2026-10-23 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | LTC-USDT | short | 2 (мало данных) | 2026-10-16 — 2026-10-30 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | normal | будни | SOL-USDT | short | 2 (мало данных) | 2026-10-30 — 2026-11-04 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | normal | будни | XRP-USDT | short | 1 (мало данных) | 2026-10-13 — 2026-10-13 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | выходные | ADA-USDT | short | 1 (мало данных) | 2026-10-24 — 2026-10-24 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | выходные | SOL-USDT | long | 1 (мало данных) | 2026-10-31 — 2026-10-31 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | off | будни | ADA-USDT | short | 1 (мало данных) | 2026-10-19 — 2026-10-19 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | off | будни | AVAX-USDT | short | 1 (мало данных) | 2026-10-13 — 2026-10-13 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | off | будни | ETH-USDT | short | 1 (мало данных) | 2026-10-30 — 2026-10-30 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | off | будни | OP-USDT | short | 1 (мало данных) | 2026-10-19 — 2026-10-19 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | off | будни | PENDLE-USDT | short | 1 (мало данных) | 2026-10-29 — 2026-10-29 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | off | будни | SOL-USDT | short | 1 (мало данных) | 2026-10-30 — 2026-10-30 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | off | будни | XRP-USDT | short | 1 (мало данных) | 2026-10-29 — 2026-10-29 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | off | выходные | AVAX-USDT | short | 1 (мало данных) | 2026-10-31 — 2026-10-31 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | off | выходные | BTC-USDT | short | 2 (мало данных) | 2026-10-31 — 2026-10-31 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | thin | будни | AVAX-USDT | long | 1 (мало данных) | 2026-10-13 — 2026-10-13 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | thin | будни | DOGE-USDT | short | 1 (мало данных) | 2026-10-13 — 2026-10-13 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | thin | будни | ETH-USDT | long | 1 (мало данных) | 2026-11-05 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | thin | будни | ETH-USDT | short | 1 (мало данных) | 2026-10-13 — 2026-10-13 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | thin | будни | HYPE-USDT | long | 1 (мало данных) | 2026-11-02 — 2026-11-02 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | thin | выходные | HYPE-USDT | long | 2 (мало данных) | 2026-11-01 — 2026-11-01 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | thin | выходные | INJ-USDT | short | 1 (мало данных) | 2026-11-01 — 2026-11-01 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | thin | выходные | LTC-USDT | short | 2 (мало данных) | 2026-10-31 — 2026-10-31 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |

## 3. Volume-zone shadow

| метрика | значение | период MSK | источник | контур | связь / n сделок |
| --- | --- | --- | --- | --- | --- |
| approach observations | 4120 | 2026-11-02 — 2026-11-07 MSK | models/zeus_live_journal.jsonl | live (env не записан) | наблюдения, не сделки; trade_id отсутствует |
| zone_shadow ticks | 4305 | 2026-11-02 — 2026-11-07 MSK | models/zeus_live_journal.jsonl | live (env не записан) | наблюдения, не сделки; trade_id отсутствует |
| in-zone observations | 865 | 2026-11-02 — 2026-11-07 MSK | models/zeus_live_journal.jsonl | live (env не записан) | наблюдения, не сделки; trade_id отсутствует |
| minimum observed distance, % | 0.0002 | 2026-11-02 — 2026-11-07 MSK | models/zeus_live_journal.jsonl | live (env не записан) | наблюдения, не сделки; trade_id отсутствует |
| zone summaries | 140 | 2026-11-03 — 2026-11-07 MSK | models/zeus_live_journal.jsonl | live (env не записан) | наблюдения, не сделки; trade_id отсутствует |
| zone errors | 0 | нет данных | models/zeus_live_journal.jsonl | live (env не записан) | наблюдения, не сделки; trade_id отсутствует |
| current snapshot (not historical count) | 8 zones; min distance 0.1395 | 2026-11-07 — 2026-11-07 MSK | models/zone_shadow_approach.json | live (env не записан) | snapshot, не сделки; без trade_id |


Approach/in-zone — число наблюдений, не число сделок. Zone rows не содержат trade_id и environment.

## 4. Воронка сигналов

| этап | значение | период MSK | источник | контур | связь / n сделок |
| --- | --- | --- | --- | --- | --- |
| scan attempts | нет данных за период | нет данных | models/zeus_live_journal.jsonl | live (env не записан) | по символам не журналируются |
| pending_signal candidates | 22410 | 2026-10-14 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | не trade-level |
| arm_summary candidate count | 21209 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | summed from arm_summary |
| successful entry records | 119 | 2026-10-13 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | live (env не записан) | n entry events |
| failed/rejected entry records | 14 | 2026-10-12 — 2026-10-19 MSK | models/zeus_live_entries.jsonl | live (env не записан) | n failure rows |
| verified exit results | 123 | 2026-10-13 — 2026-11-06 MSK | models/zeus_live_trades.jsonl | live (env не записан) | n exits with pnl |

### Отбраковки по причинам

| причина | n | период MSK | источник | контур | связь / n сделок |
| --- | --- | --- | --- | --- | --- |
| conf | 8 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| cooldown | 0 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| entries_per_tick_limit | 0 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| equity_unknown | 0 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| filters | 0 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| htf_gate | 0 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| margin | 0 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| margin_M2 | 0 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| margin_M3 | 0 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| margin_M4 | 0 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| max_open | 1246 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| other | 3782 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| plan_unreconciled | 2 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| position_value_too_large | 113 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| retest_not_triggered | 0 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| same_side_guard | 35 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| side_cluster_full | 115 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| sized_qty_zero | 4 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| symbol_conflict | 834 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |
| vol_cooldown | 0 | 2026-10-15 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | arm_summary aggregated skips |

Candidate/skip funnel starts at persisted pending_signal and arm_summary events. Per-symbol raw scan attempts are not logged.

## 5. Здоровье live и COPY_ZEUS

| показатель | значение | период MSK | источник | контур | связь / n сделок |
| --- | --- | --- | --- | --- | --- |
| observed live tick batches (inferred; group gap ≤120s) | 3060 | 2026-10-15 — 2026-11-07 MSK | models/zeus_live_journal.jsonl | live (env не записан) | не trade-level |
| tick interval min / median / max, min | 2.10 / 5.14 / 3109.46 | 2026-10-15 — 2026-11-07 MSK | models/zeus_live_journal.jsonl | live (env не записан) | не trade-level |
| journal errors | 123 | 2026-10-19 — 2026-11-05 MSK | models/zeus_live_journal.jsonl | live (env не записан) | не обязательно сделки |
| restore_tp_failed / protect_place_failed | 1163 | 2026-10-16 — 2026-10-23 MSK | models/zeus_live_trades.jsonl | live (env не записан) | protective-order diagnostics; не сделки |
| entry_failed / entry_rejected | 14 | 2026-10-12 — 2026-10-19 MSK | models/zeus_live_entries.jsonl | live (env не записан) | n failed entry records |
| reconciliation / ghost / pending_verify events | 52 | 2026-10-12 — 2026-11-05 MSK | models/zeus_live_journal.jsonl; models/zeus_live_trades.jsonl; models/zeus_live_entries.jsonl | live (env не записан) | qty_reconcile + pending_verify; не обязательно сделки |
| 100410 occurrences in readable structured rows | 5 | 2026-10-27 — 2026-10-27 MSK | models/zeus_live_journal.jsonl | контур по источнику/метке; иначе UNKNOWN | не обязательно сделки |
| COPY_ZEUS archive ticks | нет данных за период | нет данных | reports/ structured files | COPY_ZEUS / REAL read-only | не сделки |
| COPY_ZEUS errors / 100410 | нет данных за период | нет данных | reports/ structured files | COPY_ZEUS / REAL read-only | не сделки |

нет данных за период по COPY_ZEUS: encrypted Actions artifacts и console logs не входят в доступные models/reports JSON/JSONL.

## Ограничения выборки

- Paper: 434 unique ids from 457 rows/legs; source models/zeus_paper_trades.jsonl; это paper, не VST.
- Live exits: 123 rows with pnl and without pending_verify. VST=0, REAL=0, UNKNOWN=123; source models/zeus_live_trades.jsonl.
- Pending verify exits excluded from metrics: 2.
- Environment metadata отсутствует в части live rows; такие записи не классифицируются как VST.
- Zone events are observation-level and have no trade_id. Current approach-state JSON is shown only as a snapshot.
- Tick batches are inferred from journal timestamps and are not exact GitHub Actions run counts.
- COPY_ZEUS encrypted artifact is not read/decrypted by this script. Only structured reports JSON/JSONL can contribute archive telemetry.
- Malformed lines skipped: paper=0, live_trades=0, live_entries=0, live_journal=0.

## Reproducibility

Same inputs produce identical summary.md. No network, API, Secrets, current clock, workflow, trading code or state writes.
