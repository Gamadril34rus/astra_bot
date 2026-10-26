# ASTRA BOT · by Gamadril

![Bot](https://github.com/Gamadril34rus/astra_bot/actions/workflows/bot.yml/badge.svg)
![Zeus paper](https://github.com/Gamadril34rus/astra_bot/actions/workflows/zeus-paper-clock.yml/badge.svg)
![Zeus live](https://github.com/Gamadril34rus/astra_bot/actions/workflows/zeus-live-clock.yml/badge.svg)

**Автономная торговая система для крипто-фьючерсов BingX (USDT-M) на бесплатных GitHub Actions — без VPS.**

Реальные деньги выключены. Всё здесь — обучение и исследование, не финансовая рекомендация.

Канон числовых настроек: **[SETTINGS.md](SETTINGS.md)**.

## Контуры

| Контур | Что это | Статус |
|--|--|--|
| **Paper** | Симулятор с моделью издержек | Zeus 4h: канал + клин-ретест |
| **Live (VST / демо)** | Демо-счёт BingX, те же правила входа, защита на бирже | тики каждые ~5 мин; exit-engine по умолчанию **shadow** |
| **Реал** | Боевой счёт | **замок**: включение только прямым словом владельца |

Live повторяет paper-сигналы; паритет тейка **clamp RR [2.0; 2.3]** и долей **30/70** (F15, #251). Риск на сделку — лестница confidence; день ≤4% / неделя ≤6% от реального equity; kill n≥5 / PF&lt;1 (окно 20 после эпохи); heat (серия минусов → риск ×0.5).

## Как устроено

### Источник тиков

Внешний cron (cron-job.org) шлёт `workflow_dispatch` на:

- `zeus-live-clock.yml` — live-тик
- `zeus-paper-clock.yml` — paper-тик
- `bot.yml` — paper/TG-бот (пульт + сервисные команды)

GitHub `on.schedule` с контуров снят (ненадёжен). Параллельность live гасится concurrency-группой. VPS не используется.

### Порядок live-тика (сжато)

1. Поллер LIVE-пульта (`tg_live_commands`) → очередь `models/zeus_live_commands.json`
2. `apply_pending` (stop / flat / resume / reset_kill)
3. Reconcile / income tape / sync closed plans
4. Protect / restore стопов и TP; ghost-detect (биржа без плана)
5. **Exit-engine** (`ZEUS_LIVE_EXIT_ENGINE`, default `shadow`) — journal `exit_shadow`, без боевых трейл-ордеров пока не ON
6. Partial BE / catch-up / heal TP
7. Arm entries (`ZEUS_ARM=1`), если не blocked / kill
8. Save state → commit `models/*` с retry push

### Пульт (LIVE-бот)

Кнопки ReplyKeyboard → хендлеры → `enqueue` в `zeus_live_commands.json` → исполнение live-тиком ≤5–10 мин + ack в TG.

Типичные команды: сброс стоп-крана, пауза/продолжение входов, закрыть всё (с confirm), баланс, справка-прогноз.

### Shadow и флаги выхода

| env | default | смысл |
|--|--|--|
| `ZEUS_LIVE_EXIT_ENGINE` | `shadow` | `shadow` = только journal; `on` = выставлять стопы по политике; `off` = выкл |
| `ZEUS_LIVE_TRAIL_POLICY` | `atr` | `atr` (k=2×ATR14) или `ratchet_e` (ступени 0.5R) |

Перевод engine в **on** и смена trail-политики — только слово владельца после shadow-окна и вердикта.

### Роли

| роль | делает |
|--|--|
| **Исполнитель (Грок)** | ветка → тесты → PR; отчёт file:line; ops зависших ранов по standing-правилу |
| **Арена** | ревью диффа / API, вердикт merge-ready |
| **Владелец** | слово на мерж, ON/ENV, risk-числа, джобы, реал |

### Качество

Каждый PR: **quality-gates** — pytest (unit+integration) → ruff → import smoke → state-size → gitleaks. Красные gates = мерж нельзя.

### Телеметрия

TG: своды 09:00/21:00 МСК, ракеты входа/выхода с точным знаком суммы, ⚠️ на HALT / отказ защиты / ghost. Без спама по каждому фильтру сигнала.

## Workflows

| файл | роль |
|--|--|
| `zeus-live-clock.yml` | live-тик (dispatch) |
| `zeus-paper-clock.yml` | paper-тик (dispatch) |
| `bot.yml` | paper-бот / TG |
| `quality-gates.yml` | PR-ворота |
| `morning-report.yml` | сервисные своды |

Секреты — только в Actions secrets. Торговые ключи trade-only.

## Документация

- **[SETTINGS.md](SETTINGS.md)** — канон параметров (лестница, clamp, доли, kill, env)
- Индекс: `docs/INDEX.md`
- Исследование: `docs/research/` (в т.ч. правила клина Zeus)

## Права

Код публичен для прозрачности и аудита. Использование, копирование, модификация и форки — только с письменного разрешения автора (см. LICENSE).

## Дисклеймер

Учебно-исследовательский проект. Торговля фьючерсами с плечом может привести к полной потере средств. Бумага и демо; реал отключён. Автор не несёт ответственности за использование.
