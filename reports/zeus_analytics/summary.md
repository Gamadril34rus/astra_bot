# ZEUS analytics

Отчёт собран из текущего снимка структурированных файлов репозитория. Генератор scripts/zeus_analytics.py строит этот файл локально; исходные models/ и старые reports/ не меняются.
Paper и live не объединяются. В live-файлах нет явного env/environment, поэтому все live exits остаются в UNKNOWN и не считаются VST.
PF = сумма положительных pnl / абсолютная сумма отрицательных pnl. Fees показываются отдельно. Expectancy = средний pnl на сделку. Частичные paper legs объединены по id; R и fees суммируются по legs.

## 1. Стратегии

| контур | стратегия | PF | winrate, % | средний R | expectancy, USDT | fees, USDT | период | источник | n сделок |
| - | - | - | - | - | - | - | - | - | - |
| paper | channel | 4.4473 | 55.79 | 0.8370 | 1.6606 | 74.6517 | 2026-09-21 — 2026-10-30 MSK | models/zeus_paper_trades.jsonl | 380 |
| paper | wedge | 0.8161 | 40.74 | -0.0008 | -0.1481 | 10.0234 | 2026-09-22 — 2026-10-06 MSK | models/zeus_paper_trades.jsonl | 54 |
| paper | multi-top | нет данных | нет данных | нет данных | нет данных | нет данных | нет данных | models/zeus_paper_trades.jsonl | нет данных |
| VST | channel | нет данных | нет данных | нет данных | нет данных | нет данных | нет данных | models/zeus_live_trades.jsonl | нет данных |
| VST | wedge | нет данных | нет данных | нет данных | нет данных | нет данных | нет данных | models/zeus_live_trades.jsonl | нет данных |
| VST | multi-top | нет данных | нет данных | нет данных | нет данных | нет данных | нет данных | models/zeus_live_trades.jsonl | нет данных |
| REAL | channel | нет данных | нет данных | нет данных | нет данных | нет данных | нет данных | models/zeus_live_trades.jsonl | нет данных |
| REAL | wedge | нет данных | нет данных | нет данных | нет данных | нет данных | нет данных | models/zeus_live_trades.jsonl | нет данных |
| REAL | multi-top | нет данных | нет данных | нет данных | нет данных | нет данных | нет данных | models/zeus_live_trades.jsonl | нет данных |
| live / UNKNOWN | channel | 0.5147 | 23.91 | нет данных | -96.0245 | 4940.0863 | 2026-10-13 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | 92 |
| live / UNKNOWN | wedge | нет данных | нет данных | нет данных | нет данных | нет данных | нет данных | models/zeus_live_trades.jsonl | нет данных |
| live / UNKNOWN | multi-top | 1.3029 | 35.29 | нет данных | 26.0940 | 584.1273 | 2026-10-31 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | 17 (мало данных) |

VST и REAL не имеют явно маркированных закрытий в доступных live rows; значения не угадываются по настройке workflow.

## 2. Сессии

Сессия берётся по времени открытия сделки, по той же таблице raw_bucket() из live_session.py, часовой пояс MSK. Детализация: профиль × будни/выходные × символ × направление.

### Paper — сводка

| контур | профиль | день | n сделок | период | источник | n / статус |
| - | - | - | - | - | - | - |
| paper | fat | будни | 105 | 2026-09-21 — 2026-10-26 MSK | models/zeus_paper_trades.jsonl | 105 |
| paper | fat | выходные | нет данных за период | нет данных | models/zeus_paper_trades.jsonl | нет данных |
| paper | normal | будни | 107 | 2026-09-21 — 2026-10-29 MSK | models/zeus_paper_trades.jsonl | 107 |
| paper | normal | выходные | 31 | 2026-10-03 — 2026-10-24 MSK | models/zeus_paper_trades.jsonl | 31 |
| paper | thin | будни | 43 | 2026-09-21 — 2026-10-30 MSK | models/zeus_paper_trades.jsonl | 43 |
| paper | thin | выходные | 32 | 2026-09-27 — 2026-10-11 MSK | models/zeus_paper_trades.jsonl | 32 |
| paper | off | будни | 22 | 2026-10-02 — 2026-10-26 MSK | models/zeus_paper_trades.jsonl | 22 |
| paper | off | выходные | 94 | 2026-09-26 — 2026-10-24 MSK | models/zeus_paper_trades.jsonl | 94 |

### Paper — символ × направление

| контур | профиль | день | символ | направление | n сделок | период | источник | n / статус |
| - | - | - | - | - | - | - | - | - |
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

### Live / UNKNOWN — сводка (не VST)

| контур | профиль | день | n сделок | период | источник | n / статус |
| - | - | - | - | - | - | - |
| live / UNKNOWN | fat | будни | 61 | нет данных | models/zeus_live_trades.jsonl | 61 |
| live / UNKNOWN | fat | выходные | нет данных за период | нет данных | models/zeus_live_trades.jsonl | нет данных |
| live / UNKNOWN | normal | будни | 19 (мало данных) | нет данных | models/zeus_live_trades.jsonl | 19 (мало данных) |
| live / UNKNOWN | normal | выходные | 7 (мало данных) | нет данных | models/zeus_live_trades.jsonl | 7 (мало данных) |
| live / UNKNOWN | thin | будни | 7 (мало данных) | нет данных | models/zeus_live_trades.jsonl | 7 (мало данных) |
| live / UNKNOWN | thin | выходные | 4 (мало данных) | нет данных | models/zeus_live_trades.jsonl | 4 (мало данных) |
| live / UNKNOWN | off | будни | 11 (мало данных) | нет данных | models/zeus_live_trades.jsonl | 11 (мало данных) |
| live / UNKNOWN | off | выходные | 7 (мало данных) | нет данных | models/zeus_live_trades.jsonl | 7 (мало данных) |

### Live / UNKNOWN — символ × направление

| контур | профиль | день | символ | направление | n сделок | период | источник | n / статус |
| - | - | - | - | - | - | - | - | - |
| live / UNKNOWN | fat | будни | AAVE-USDT | нет данных | 1 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | ADA-USDT | нет данных | 4 (мало данных) | 2026-10-19 — 2026-10-29 MSK | models/zeus_live_trades.jsonl | 4 (мало данных) |
| live / UNKNOWN | fat | будни | APT-USDT | нет данных | 1 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | ARB-USDT | нет данных | 1 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | ATOM-USDT | нет данных | 1 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | AVAX-USDT | нет данных | 4 (мало данных) | 2026-10-27 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 4 (мало данных) |
| live / UNKNOWN | fat | будни | BCH-USDT | нет данных | 1 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | BTC-USDT | нет данных | 4 (мало данных) | 2026-10-29 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | 4 (мало данных) |
| live / UNKNOWN | fat | будни | DOGE-USDT | нет данных | 3 (мало данных) | 2026-10-13 — 2026-10-27 MSK | models/zeus_live_trades.jsonl | 3 (мало данных) |
| live / UNKNOWN | fat | будни | DOT-USDT | нет данных | 3 (мало данных) | 2026-10-19 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 3 (мало данных) |
| live / UNKNOWN | fat | будни | ETH-USDT | нет данных | 2 (мало данных) | 2026-11-05 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | fat | будни | ETH-USDT | нет данных | 7 (мало данных) | 2026-10-13 — 2026-11-03 MSK | models/zeus_live_trades.jsonl | 7 (мало данных) |
| live / UNKNOWN | fat | будни | FIL-USDT | нет данных | 1 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | HYPE-USDT | нет данных | 1 (мало данных) | 2026-11-02 — 2026-11-02 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | INJ-USDT | нет данных | 1 (мало данных) | 2026-11-04 — 2026-11-04 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | INJ-USDT | нет данных | 2 (мало данных) | 2026-11-03 — 2026-11-03 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | fat | будни | LINK-USDT | нет данных | 2 (мало данных) | 2026-10-20 — 2026-10-27 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | fat | будни | LTC-USDT | нет данных | 2 (мало данных) | 2026-10-20 — 2026-10-27 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | fat | будни | OP-USDT | нет данных | 1 (мало данных) | 2026-10-13 — 2026-10-13 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | OP-USDT | нет данных | 2 (мало данных) | 2026-10-20 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | fat | будни | PENDLE-USDT | нет данных | 1 (мало данных) | 2026-10-29 — 2026-10-29 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | SOL-USDT | нет данных | 3 (мало данных) | 2026-10-23 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | 3 (мало данных) |
| live / UNKNOWN | fat | будни | SOL-USDT | нет данных | 5 (мало данных) | 2026-10-27 — 2026-11-03 MSK | models/zeus_live_trades.jsonl | 5 (мало данных) |
| live / UNKNOWN | fat | будни | SUI-USDT | нет данных | 2 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | fat | будни | TAO-USDT | нет данных | 1 (мало данных) | 2026-10-29 — 2026-10-29 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | fat | будни | XRP-USDT | нет данных | 5 (мало данных) | 2026-10-13 — 2026-10-27 MSK | models/zeus_live_trades.jsonl | 5 (мало данных) |
| live / UNKNOWN | normal | будни | 1000PEPE-USDT | нет данных | 1 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | AAVE-USDT | нет данных | 1 (мало данных) | 2026-10-16 — 2026-10-16 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | APT-USDT | нет данных | 1 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | ARB-USDT | нет данных | 1 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | AVAX-USDT | нет данных | 1 (мало данных) | 2026-10-15 — 2026-10-15 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | BNB-USDT | нет данных | 1 (мало данных) | 2026-10-29 — 2026-10-29 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | BTC-USDT | нет данных | 1 (мало данных) | 2026-11-03 — 2026-11-03 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | DOT-USDT | нет данных | 1 (мало данных) | 2026-10-23 — 2026-10-23 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | DOT-USDT | нет данных | 1 (мало данных) | 2026-10-16 — 2026-10-16 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | ETH-USDT | нет данных | 2 (мало данных) | 2026-10-29 — 2026-11-04 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | normal | будни | ETH-USDT | нет данных | 1 (мало данных) | 2026-10-19 — 2026-10-19 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | HYPE-USDT | нет данных | 1 (мало данных) | 2026-10-28 — 2026-10-28 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | HYPE-USDT | нет данных | 2 (мало данных) | 2026-11-02 — 2026-11-02 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | normal | будни | LTC-USDT | нет данных | 1 (мало данных) | 2026-10-16 — 2026-10-16 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | NEAR-USDT | нет данных | 1 (мало данных) | 2026-10-16 — 2026-10-16 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | SOL-USDT | нет данных | 1 (мало данных) | 2026-11-02 — 2026-11-02 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | будни | SUI-USDT | нет данных | 1 (мало данных) | 2026-10-16 — 2026-10-16 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | выходные | ADA-USDT | нет данных | 1 (мало данных) | 2026-10-24 — 2026-10-24 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | выходные | APT-USDT | нет данных | 1 (мало данных) | 2026-10-17 — 2026-10-17 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | выходные | AVAX-USDT | нет данных | 1 (мало данных) | 2026-10-18 — 2026-10-18 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | выходные | BTC-USDT | нет данных | 1 (мало данных) | 2026-10-24 — 2026-10-24 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | выходные | INJ-USDT | нет данных | 1 (мало данных) | 2026-10-18 — 2026-10-18 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | выходные | LTC-USDT | нет данных | 1 (мало данных) | 2026-10-18 — 2026-10-18 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | normal | выходные | SOL-USDT | нет данных | 1 (мало данных) | 2026-10-31 — 2026-10-31 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | off | будни | ADA-USDT | нет данных | 1 (мало данных) | 2026-10-29 — 2026-10-29 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | off | будни | AVAX-USDT | нет данных | 2 (мало данных) | 2026-10-13 — 2026-10-29 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | off | будни | BTC-USDT | нет данных | 1 (мало данных) | 2026-11-04 — 2026-11-04 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | off | будни | ETH-USDT | нет данных | 2 (мало данных) | 2026-10-30 — 2026-10-30 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | off | будни | PENDLE-USDT | нет данных | 1 (мало данных) | 2026-10-29 — 2026-10-29 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | off | будни | SOL-USDT | нет данных | 3 (мало данных) | 2026-10-30 — 2026-11-04 MSK | models/zeus_live_trades.jsonl | 3 (мало данных) |
| live / UNKNOWN | off | будни | XRP-USDT | нет данных | 1 (мало данных) | 2026-10-29 — 2026-10-29 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | off | выходные | AVAX-USDT | нет данных | 1 (мало данных) | 2026-11-01 — 2026-11-01 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | off | выходные | DOT-USDT | нет данных | 1 (мало данных) | 2026-10-17 — 2026-10-17 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | off | выходные | ETH-USDT | нет данных | 1 (мало данных) | 2026-10-31 — 2026-10-31 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | off | выходные | HYPE-USDT | нет данных | 2 (мало данных) | 2026-11-01 — 2026-11-01 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | off | выходные | LTC-USDT | нет данных | 1 (мало данных) | 2026-10-31 — 2026-10-31 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | off | выходные | SOL-USDT | нет данных | 1 (мало данных) | 2026-10-31 — 2026-10-31 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | thin | будни | AVAX-USDT | нет данных | 1 (мало данных) | 2026-10-13 — 2026-10-13 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | thin | будни | BCH-USDT | нет данных | 1 (мало данных) | 2026-10-14 — 2026-10-14 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | thin | будни | DOGE-USDT | нет данных | 1 (мало данных) | 2026-10-13 — 2026-10-13 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | thin | будни | ETH-USDT | нет данных | 1 (мало данных) | 2026-11-03 — 2026-11-03 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | thin | будни | ETH-USDT | нет данных | 1 (мало данных) | 2026-10-13 — 2026-10-13 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | thin | будни | HYPE-USDT | нет данных | 1 (мало данных) | 2026-11-02 — 2026-11-02 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | thin | будни | INJ-USDT | нет данных | 1 (мало данных) | 2026-11-02 — 2026-11-02 MSK | models/zeus_live_trades.jsonl | 1 (мало данных) |
| live / UNKNOWN | thin | выходные | BTC-USDT | нет данных | 2 (мало данных) | 2026-10-31 — 2026-10-31 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |
| live / UNKNOWN | thin | выходные | LTC-USDT | нет данных | 2 (мало данных) | 2026-10-31 — 2026-10-31 MSK | models/zeus_live_trades.jsonl | 2 (мало данных) |

### VST

нет данных за период: ни одна доступная live exit row не содержит явного признака VST.

## 3. Volume-zone shadow

| метрика | значение | период | источник | контур | связь / n сделок |
| - | - | - | - | - | - |
| approach observations | нет данных за период | нет данных | models/zeus_live_journal.jsonl | live (env не записан) | zone journal пуст |
| zone_shadow ticks | нет данных за период | нет данных | models/zeus_live_journal.jsonl | live (env не записан) | zone journal пуст |
| in-zone observations | нет данных за период | нет данных | models/zeus_live_journal.jsonl | live (env не записан) | zone journal пуст |
| minimum observed distance, % | нет данных за период | нет данных | models/zeus_live_journal.jsonl | live (env не записан) | zone journal пуст |
| zone summaries | нет данных за период | нет данных | models/zeus_live_journal.jsonl | live (env не записан) | zone journal пуст |
| zone errors | нет данных за период | нет данных | models/zeus_live_journal.jsonl | live (env не записан) | zone journal пуст |
| current approach-state snapshot (не исторический счётчик) | 9 зон; минимум 0.1159% | 2026-11-05 — 2026-11-05 MSK | models/zone_shadow_approach.json | live (env не записан) | snapshot без trade_id; не сделки |

models/zeus_live_journal.jsonl пуст. Поэтому исторические подходы к зонам, in-zone наблюдения и tick events не могут быть посчитаны. zone_shadow_approach.json — только текущий snapshot: он не доказывает число подходов за историю и не имеет trade_id.

## 4. Воронка сигналов

| этап | значение | период | источник | контур | связь / n сделок |
| - | - | - | - | - | - |
| scan attempts | нет данных за период | нет данных | models/zeus_live_journal.jsonl | live (env не записан) | per-symbol scan events не пишутся |
| pending_signal candidates | нет данных за период | нет данных | models/zeus_live_journal.jsonl | live (env не записан) | journal пуст |
| arm_summary candidate count | нет данных за период | нет данных | models/zeus_live_journal.jsonl | live (env не записан) | journal пуст |
| successful entry records | 114 | 2026-10-13 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | live (env не записан) | n entry events |
| failed/rejected entry records | 14 | 2026-10-12 — 2026-10-19 MSK | models/zeus_live_entries.jsonl | live (env не записан) | n failure rows |
| verified exit results | 116 | 2026-10-13 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | live (env не записан) | n exits with pnl |

Отбраковки по причинам: нет данных за период, потому что arm_skip / arm_summary events должны находиться в zeus_live_journal.jsonl, а этот журнал сейчас пуст.

## 5. Здоровье live и COPY_ZEUS

| показатель | значение | период | источник | контур | связь / n сделок |
| - | - | - | - | - | - |
| live tick batches | нет данных за период | нет данных | models/zeus_live_journal.jsonl | live (env не записан) | journal пуст; это не ноль тиков |
| live tick interval | нет данных за период | нет данных | models/zeus_live_journal.jsonl | live (env не записан) | нет per-tick timestamps |
| restore_tp_failed | 1160 | 2026-10-16 — 2026-10-23 MSK | models/zeus_live_trades.jsonl | live (env не записан) | protective-order diagnostics |
| protect_place_failed | 3 | 2026-10-22 — 2026-10-22 MSK | models/zeus_live_trades.jsonl | live (env не записан) | protective-order diagnostics |
| entry_failed / entry_rejected | 14 | 2026-10-12 — 2026-10-19 MSK | models/zeus_live_entries.jsonl | live (env не записан) | failed entry records |
| qty_reconcile | 24 | 2026-10-24 — 2026-11-05 MSK | models/zeus_live_trades.jsonl | live (env не записан) | reconciliation events |
| pending_verify | 3 | 2026-10-12 — 2026-10-28 MSK | models/zeus_live_trades.jsonl; models/zeus_live_entries.jsonl | live (env не записан) | unresolved verification |
| 100410 occurrences in structured rows | 0 | нет данных: matches отсутствуют | models/zeus_live_trades.jsonl; models/zeus_live_entries.jsonl; models/zeus_live_journal.jsonl | UNKNOWN | 0 occurrences in inspected rows |
| COPY_ZEUS archive ticks | нет данных за период | нет данных | reports/**/*.json[l] | COPY_ZEUS / REAL read-only | no structured archive report |
| COPY_ZEUS 100410 errors | нет данных за период | нет данных | reports/**/*.json[l] | COPY_ZEUS / REAL read-only | no structured archive report |

Интервалы между live ticks нельзя вывести из trade events: они не являются журналом каждого workflow run. Archive ticks нельзя вывести из зашифрованного Actions artifact без отдельного структурированного report JSON/JSONL.

## Ограничения выборки

- Paper: 434 уникальных id из 457 строк/legs; период 2026-09-21 — 2026-10-30 MSK; source models/zeus_paper_trades.jsonl.
- Live: 116 закрытых exits с pnl; период 2026-10-13 — 2026-11-05 MSK; env=UNKNOWN для всех 116; source models/zeus_live_trades.jsonl.
- Live entry events: 114; failed/rejected rows: 14; pending_verify: 3.
- Zone snapshot: 5 symbols, 9 zones, minimum stored distance 0.1159%; source models/zone_shadow_approach.json.
- Отсутствие journal events означает «нет данных», а не ноль тиков или сигналов.
- COPY_ZEUS: зашифрованные artifacts и workflow console logs не являются входами этого read-only генератора.

## Воспроизводимость

Скрипт не использует сеть, API, Secrets, текущее время или workflows. Один и тот же снимок входных файлов даёт одинаковый summary.md. Все значения выше привязаны к указанным файлам и периоду; при следующем ручном запуске отчёт пересобирается.
