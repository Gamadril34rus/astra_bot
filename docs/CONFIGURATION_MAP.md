# Карта конфигурации ASTRA BOT

Срез: **05.11.2026**. Этот документ показывает, где живут настройки разных контуров и какой источник главнее. Он не меняет конфигурацию и не включает никаких режимов.

## Главное правило: конфиги не взаимозаменяемы

**Не переносить значения из `config/settings.yaml` в Zeus и не переносить значения Zeus обратно в общий конфиг.** Это разные поколения и контуры приложения с разными назначениями и моделью риска.

| Источник | Для чего предназначен | Что важно |
|---|---|---|
| `config/settings.yaml` | Общая конфигурация ASTRA BOT | Общие параметры приложения и его стратегий. Значения `risk_per_trade=0.004` (0,4%), `daily_loss_limit=0.02` (2%) и `weekly_loss_limit=0.04` (4%) **не являются каноном Zeus**. |
| `config/paper_zeus_clock.yaml` | Профиль бумажного Zeus | Явно помечен в файле как paper-only профиль. Его параметры используются бумажным runner, а не как настройки live. |
| `SETTINGS.md` | Канон Zeus | Числовые правила Zeus, их источники, code defaults и фактические значения workflow на дату среза. Для Zeus это основной справочник. |
| `.github/workflows/zeus-live-clock.yml` | Фактическое окружение live-clock | Явно задаёт часть переменных процесса. Остальные передаёт из GitHub Actions Secrets. |
| GitHub Actions Secrets | Секреты и owner-controlled флаги | Сами значения хранятся в настройках репозитория, а не в Git. Workflow только передаёт их процессу. |
| `.github/workflows/copy-zeus-archive.yml` + `scripts/copy_zeus_archive.py` | COPY_ZEUS, этап 1 | Отдельный real-only **read-only архив**. Использует существующие BingX API secrets; создание, изменение и закрытие ордеров отсутствуют. |

## Как выбирается фактическое значение

1. Если workflow передаёт переменную в секции `env`, процесс получает именно её.
2. Если переменная не передана, применяется значение по умолчанию из кода.
3. Для секретных значений workflow передаёт содержимое GitHub Actions Secrets; оно не раскрывается в YAML и не должно попадать в журналы.
4. Некоторые настройки имеют зависимое поведение. В частности, при `ZEUS_SESSION_PROFILE=1` значения BE/TP1 выбираются по профилю часа МСК. Значения `ZEUS_BE_TRIGGER_R` и `ZEUS_TP1_RR` из workflow не заменяют профиль, пока не включён `ZEUS_SESSION_LOCK_ENV=1`.

Полная таблица чисел, defaults и live-overrides находится в [SETTINGS.md](../SETTINGS.md).

## Секреты и флаги, которые передаются владельцем

| Secret / значение | Где используется | Назначение |
|---|---|---|
| `ZEUS_LIVE` | `zeus-live-clock.yml` | Защитный допуск live-clock. Если значение не `1`, шаг guard завершает запуск без live-адаптера. |
| `ZEUS_VST`, `ZEUS_MAINNET` | `zeus-live-clock.yml` | Выбор среды BingX. Для mainnet необходима явная конфигурация, соответствующая guard в коде; не считать пустой secret разрешением на real. |
| `ZEUS_ARM` | `zeus-live-clock.yml` | Разрешение arm-входов. Это отдельный флаг от запуска самого clock. |
| `BINGX_API_KEY`, `BINGX_API_SECRET` | live-clock и COPY_ZEUS archive | Учетные данные BingX. Значения — только в Secrets. Для COPY_ZEUS используются текущие ключи, новый ключ не создаётся. |
| `TELEGRAM_LIVE_BOT_TOKEN`, `TELEGRAM_ADMIN_ID`, `TELEGRAM_CHAT_ID` | live-clock | Telegram-команды и уведомления live-контура. |
| `TG_TEST` | live-clock | Дополнительный Telegram test flag, передаваемый workflow. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_ID` | paper-clock | Telegram для бумажного статуса. |
| `GH_TOKEN` | COPY_ZEUS archive | В workflow берётся из встроенного `github.token`; отдельный пользовательский secret для него не нужен. |

Не вставлять секреты в YAML, README, отчёты, commit messages или диагностические снимки. Наличие secret само по себе не означает, что владелец разрешил переключить окружение.

## Важные фактические значения live-workflow

На 05.11.2026 `zeus-live-clock.yml` содержит:

- `ENVIRONMENT=live`, но запуск дополнительно защищён secret `ZEUS_LIVE`.
- VST/mainnet выбираются флагами `ZEUS_VST` и `ZEUS_MAINNET`; значения этих Secrets не видны из репозитория.
- `ZONE_SHADOW=1`, `ZEUS_SESSION_PROFILE=1`.
- `ZEUS_LIVE_EXIT_ENGINE=on`, хотя default модуля — `shadow`.
- `ZEUS_LIVE_TRAIL_POLICY=atr`.
- `ZEUS_LIVE_FORCE_LEVERAGE=6`, `ZEUS_LIVE_MAX_LEVERAGE=10`.
- `ZEUS_KILL_PF_WINDOW=20`.
- `ZEUS_PF_SKIP_SCRATCHED=1`, `ZEUS_PF_INCLUDE_PARTIAL=1`.

Live workflow запускается через `workflow_dispatch`. Внешний cron-job.org вызывает его примерно раз в 5 минут. GitHub schedule в live workflow отсутствует.

## COPY_ZEUS — отдельный read-only контур

`copy-zeus-archive.yml` имеет `workflow_dispatch`; внешний cron-job.org вызывает его каждые 30 минут на минутах **07 и 37**. Workflow выставляет `COPYZEUS_ENV=real`, загружает предыдущий зашифрованный архив, выполняет разрешённые GET-запросы, шифрует архив и публикует artifact.

COPY_ZEUS не наследует торговые настройки Zeus и не копирует сделки. Это сбор и хранение read-only данных реального счёта — первый инфраструктурный этап, а не разрешение торговать или зеркалировать позиции.

## Чего не делать

- Не копировать общие risk-значения из `config/settings.yaml` в Zeus.
- Не считать paper-профиль live-профилем.
- Не трактовать code default как фактическое runtime-значение, если workflow передаёт override.
- Не считать переменные workflow Secrets известными по YAML: YAML показывает только имя секрета.
- Не включать `ZEUS_LIVE`, `ZEUS_ARM`, mainnet или exit-engine ON без отдельного слова владельца.
- Не редактировать `models/` и журналы ради документации.
