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

## Backup (P3)

Compose-сервис `backup` в профиле `backup`, `restart: "no"`. На хосте:

```cron
0 3 * * * cd /opt/astra_bot && docker compose --profile backup run --rm backup
```

Снимок `models/` (не в git):

```bash
mkdir -p models_archive
tar -czf "models_archive/models_$(date -u +%Y%m%dT%H%MZ).tar.gz" models
```

`models_archive/` в `.gitignore`. Каталог `models/` в корне **не трогаем** этим PR.

## Python / CI

Везде **3.12**: Dockerfile, `pyproject.toml requires-python`. GitHub App этого PR
не имеет `workflows` permission — содержимое quality-gates (coverage, mypy,
pip-audit, python 3.12) лежит в `docs/quality-gates.p0p2.yml`. Владелец
копирует его в `.github/workflows/quality-gates.yml` и ставит
`python-version: "3.12"` в `bot.yml` / `morning-report.yml` /
`strategy-lab.yml` / `market-aware-smoke.yml`.
