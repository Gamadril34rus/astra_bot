# Операционный чеклист

## BingX API keys (P0.5) — до любого live

Ключи **не нужны** для paper (публичный рынок). Для баланса / будущего live:

| Право | Paper | Перед live | Всегда |
|---|---|---|---|
| Read | минимум, если ключ заведён | ✅ | ✅ |
| Trade | ❌ | ✅ только после ручного решения | не включать «на всякий» |
| Withdraw | ❌ | ❌ | **НИКОГДА** |

Проверка: кабинет BingX → API Management → permissions. Withdraw off. Секрет только в GitHub Secrets / `.env` (файл в `.gitignore`).

Чеклист перед live (live в этом ТЗ **не включаем**):

- [ ] Withdraw выключен
- [ ] IP allowlist, если биржа даёт
- [ ] `ENABLE_LIVE_ORDERS` не задан
- [ ] `ASTRA_API_KEY` задан при `ENVIRONMENT=production`
- [ ] readiness-gate не пройден → ордера не шлём

## Save-state (владелец, `.github/workflows/bot.yml`)

GitHub App этого PR **не может** править workflows. В Restore/Save-state
сейчас нет `models/paper_ledger.jsonl`. Добавить рядом с
`models/paper_positions.json` / `models/paper_trades.jsonl`:

```
models/paper_ledger.jsonl
```

Без этого ledger не переживает CI-сессию; запись fail-open (`record_best_effort`),
торговля не падает.

## Backup (P3)

Compose-сервис `backup` в профиле `backup`, `restart: "no"` (не `restart: daily` —
такого ключа в Compose нет). На хосте:

```cron
0 3 * * * cd /opt/astra_bot && docker compose --profile backup run --rm backup
```

Снимок `models/` (не в git):

```bash
mkdir -p models_archive
tar -czf "models_archive/models_$(date -u +%Y%m%dT%H%MZ).tar.gz" models
```

В `.gitignore`: `models_archive/`, `models_archive_*/`, `models_archive_*.tar.gz`
(покрывает и `models_archive_20260908/`). Каталог `models/` в корне **не трогаем** этим PR.

## Python / CI

`requires-python = ">=3.11"` (совместимо с текущим Actions `python-version: "3.11"`).
Dockerfile и recipe `docs/quality-gates.p0p2.yml` — **3.12** (включает владелец).
GitHub App этого PR не имеет `workflows` permission — содержимое quality-gates
(coverage, mypy, pip-audit, python 3.12) лежит в `docs/quality-gates.p0p2.yml`.
Владелец копирует его в `.github/workflows/quality-gates.yml` и ставит
`python-version: "3.12"` в `bot.yml` / `morning-report.yml` /
`strategy-lab.yml` / `market-aware-smoke.yml`.
