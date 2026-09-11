# HTTP API auth (P0.1)

Мутирующие и чувствительные эндпоинты FastAPI закрыты ключом.

| Путь | Доступ |
|---|---|
| `/`, `/health`, `/ping` | открыты (healthcheck / оркестратор) |
| `/telegram/webhook` | открыт (Telegram не шлёт наш ключ) |
| `/tick`, `/train`, `/self_play`, `/retrain`, `/status`, `/metrics` | ключ |

## Политика

1. Задан `ASTRA_API_KEY` → заголовок `X-API-Key` или `Authorization: Bearer …` должен совпасть (сравнение с постоянным временем).
2. Ключ не задан и `ENVIRONMENT` ∈ {paper, development, dev, test, …} → доступ открыт (локально / CI / GitHub Actions worker).
3. Ключ не задан и `ENVIRONMENT` ∈ {production, prod, live} → **401**. Fail-closed.

Секрет только в runtime env / GitHub Secrets. В git, логи и README не попадает.

```bash
curl -H "X-API-Key: $ASTRA_API_KEY" http://127.0.0.1:8000/status
```
