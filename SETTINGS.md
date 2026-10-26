# SETTINGS — канон live/paper Zeus

Не менять без **слова владельца**. Источник истины — код + эта таблица.
Дата среза документации: 26.10.2026.

## Параметры

| параметр | значение | file:line | слово / история |
|--|--|--|--|
| CONF_RISK_LADDER | (0.88→3%), (0.80→2%), (0.70→1.5%) | `astra_bot/decision/live_arm.py:25` | риск % от confidence |
| clamp take RR | [2.0, 2.3] | `exit_plan.py:82–83`, `clamp_take_rr` | прибыльная бумажная эра |
| доли TP (частички) | **30% / 70%** (`PLAN_TP_FRACTIONS`) | `exit_plan.py:150` | канон утверждён словом владельца **15–16.10**; live-дрейф 40/60 устранён паритетом **F15 24.10 (#251)** |
| cooldown входа | 20 мин `strategy\|symbol\|side` | `live_arm.py:73` `COOLDOWN_MS` | parity с paper |
| vol-анти-дребезг | **30 мин** после VOL_EXPANSION | `reentry_guard.py:22` `VOL_EXPANSION_COOLDOWN_SEC=1800` | owner 15.10; shared paper+live; idempotent until=closed_ts+COOLDOWN |
| окно свежести сигнала | 45 мин | `live_arm_wiring.py:92` `FRESH_MAX_SEC` | stale retest → skip |
| MAX_OPEN | 5 | `live_arm.py:68` | не менять до данных воронки |
| SAME_SIDE_SOFT_CAP | 3 | `live_arm.py:71` | |
| day / week loss | 4% / 6% от **реального** equity | `live_risk.py:23–24` | day/week от кошелька, не HARD_CAP |
| kill PF | n≥5 и PF&lt;1 | `live_risk.py` `check_pf_kill` | пороги не менять |
| kill window | `ZEUS_KILL_PF_WINDOW=20` | `live_risk.py` | owner 20.10; пусто/0 → cumulative |
| kill epoch | `1792568306.3301392` | `models/zeus_kill_epoch.json` | после #240 RAW-gate защиты |
| FORCE_LEVERAGE | 6 | `.github/workflows/zeus-live-clock.yml` env | 3→6; риск % на сделку не менять |
| MAX_LEVERAGE | 10 | yml + `LiveBrokerConfig` | потолок лестницы A7 |
| LEVERAGE_LADDER | conf/ev → rung (…10/5/3) | `live_arm.py:28–` | byte-parity `trading_engine` |
| exit engine | `ZEUS_LIVE_EXIT_ENGINE=shadow\|on\|off`, default **shadow** | `live_exit_engine.py:3,39` | #238; ON только словом владельца |
| trail policy | `ZEUS_LIVE_TRAIL_POLICY=atr\|ratchet_e`, default **atr** | `live_exit_engine.py:143–144` | F18; ratchet shadow parallel |
| trailing ATR | k=2, period 14, TF 4h (fallback 1h) | `live_exit_engine` | paper parity |
| MAE_CUT | 0.9R × leverage scale 0.75 | exit engine / paper | |
| MAX_HOLD | 48h | `live_exit_engine` | |
| VOL_EXPANSION | ratio 2.0 | `live_exit_engine` | + reentry_guard 30м |
| D1 structural | buffer_pct 0.003, only tighten | `structural_tighten` | |
| BE после TP1 / MFE≥0.8R | однократно, target = net BE; F10-defer до валидной цены | live_broker / clock | |
| prior_r rank | P7 sort `prior_r` desc | `live_arm.rank_candidates`, `meta_strategy.candidate_prior_r` | owner 19.10 «лучшие из лучших» |
| TP в hedge | **не** слать `reduceOnly` на TP (код 109400) | `live_broker` | |
| стоп | `STOP_MARKET` + `closePosition=true` | protect path #240 | |
| ghost position | биржа без плана → journal + ⚠️ | `detect_ghost_positions` | F23 |

## Env (live-clock workflow)

| переменная | типичное | смысл |
|--|--|--|
| `ZEUS_ARM` | 1 | разрешить arm-входы |
| `ZEUS_LIVE_FORCE_LEVERAGE` | 6 | фиксированное плечо входа |
| `ZEUS_LIVE_MAX_LEVERAGE` | 10 | потолок |
| `ZEUS_KILL_PF_WINDOW` | 20 | скользящее окно PF |
| `ZEUS_LIVE_EXIT_ENGINE` | shadow | shadow / on / off |
| `ZEUS_LIVE_TRAIL_POLICY` | atr | atr / ratchet_e |

## Запреты (операционные)

- Мерж PR, ON exit-engine, смена risk-чисел, Disable/Enable джоб, реал — **только слово владельца**.
- `models/` руками не править (кроме явного state-коммита по слову).
- Force-push и отключение gitleaks запрещены.
