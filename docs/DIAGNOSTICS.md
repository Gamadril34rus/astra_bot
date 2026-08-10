# ASTRA BOT — Диагностика и оптимизация

Дата проверки: **2026-08-10**. Ветка: `arena/019fe76b-astra-bot`.

## 1. Краткое резюме

Репозиторий содержит зрелый по замыслу каркас автономного торгового бота
(systemd/Docker/Render/Postgres/Redis/Prometheus/Grafana, выделенные risk/execution/paper/ML-движки),
но в коде до начала работ было **несколько критических дефектов, которые
делали невозможным запуск или приводили к неверной торговой логике**:

- синтаксически нерабочий `scripts/daily_report.py` (битые кавычки) и
  `data/database.py` (отсутствовал открывающий `"""`);
- runtime-`NameError` в боевом risk-движке (`ROUND_DOWN`, `timedelta`, `Any`,
  неопределённая переменная `side` в `update_position_price`);
- неожидаемое поведение risk-мультипликатора: при нулевой просадке бот
  снижал риск до 0.75, а пороги просадки сравнивались в разных единицах
  измерения (доли vs проценты);
- критическая ошибка накопления unrealized PnL в бэктестере
  (`equity += unrealized` на каждом тике), которая экспоненциально раздувала
  кривую капитала;
- дублирование дерева конфигов и пакета данных (`astra_bot/config/`,
  `astra_bot/data/_init_.py`, корневой `data/`);
- несовпадение команды запуска в `render.yaml` с фактическим FastAPI-приложением;
- утечка фоновых `asyncio.create_task` (сборщик мусора мог удалить их до
  завершения);
- ML-зависимости (`scikit-learn`, `lightgbm`) были закомментированы в
  `requirements.txt`, но жёстко импортировались на верхнем уровне модуля —
  бот не стартовал без них;
- 1455 замечаний `ruff`, отсутствие конфигурации линтера в репо,
  `pytest.ini` вместо современного `pyproject.toml`;
- устаревшие `@app.on_event` и `datetime.utcnow()`, пустой health-check
  Dockerfile, неработающие ссылки в README (`settings.yaml.example`).

После правок: **119 тестов проходят успешно**, `ruff check .` —
**All checks passed!**, web-приложение поднимается и отвечает на
`/health`, `/status`, `/tick`.

## 2. Метрики «до / после»

| Метрика | До | После |
| --- | --- | --- |
| Падающие при импорте модули | `models.py`, `risk_engine.py`, `regime_detector.py`, `daily_report.py`, `data/database.py` | 0 |
| `pytest` | не запускался (нет `sklearn`) | **119 passed** |
| `ruff check .` (errors) | 1455 | 0 (`All checks passed!`) |
| Дубли директорий/конфигов | `data/` + `astra_bot/data/`, `config/` + `astra_bot/config/` | один источник |
| Конфигурация инструментов качества | `pytest.ini`, нет `pyproject.toml` | `pyproject.toml` |
| CI | отсутствовал | GitHub Actions (`test` + `lint`) + Dependabot |

## 3. Критические дефекты и исправления

### 3.1. Нерабочие файлы (SyntaxError / ImportError)

- `scripts/daily_report.py` — 8 строк вида `report.append("*...:*')` со
  смешанными кавычками. Файл был полностью нечитаем интерпретатором и не
  мог использоваться в кроне Render. Исправлены концы строк; добавлен
  недостающий атрибут `SystemState.total_pnl_pct` и свойство
  `RiskEngine.daily_pnl`, которых не хватало в отчёте.
- `data/database.py` — отсутствовал открывающий `"""` докстринга (Python
  видел первый литерал только в строке 3 и падал с `SyntaxError`). Сам
  каталог был дублем пакета `astra_bot/data/` с ошибочным `_init_.py`
  вместо `__init__.py`. Дубль удалён, `astra_bot/data/__init__.py`
  переименован корректно.
- `astra_bot/core/models.py` — отсутствовали импорты `ROUND_DOWN` и
  `timedelta`, которые использовались в `Instrument.format_quantity`,
  `Instrument.format_price` и `NewsEvent.__post_init__`. Любой вызов этих
  методов кидал `NameError`.
- `astra_bot/engines/regime_detector.py` — `Dict[str, Any]` без импорта `Any`.
- `astra_bot/engines/risk_engine.py`:
  - `update_position_price` ссылался на несуществующую переменную `side`;
  - `_check_drawdown_state` вызывал корутину `events.emit_async(...)` без
    `await`, из-за чего возникал `RuntimeWarning: coroutine 'emit_async'
    was never awaited` и события риск-стопа молча терялись;
  - в файле был мёртвый `daily_limit` в `daily_loss_pct`.

### 3.2. Логика risk/position sizing

- **Множитель риска при 0% просадки был 0.75 вместо 1.0.** Старый код
  шёл по списку порогов `[0, 0.03, 0.05, 0.08]` и возвращал множитель
  *первого непройденного* порога, то есть для нулевой просадки это было
  значение уровня 3%. В `SystemState.get_risk_multiplier` была обратная,
  но тоже неверная реализация. Переписано в обоих местах: пороги
  перебираются по возрастанию, берётся множитель последнего преодолённого
  порога, а единицы измерения приведены к процентам (`threshold_pct =
  drawdown * 100`).
- **Сравнение просадки в разных единицах.** `RiskConfig.hard_drawdown`
  хранится как доля (`0.08`), а `RiskEngine.current_drawdown` — в
  процентах (`8.0`). В коде было `dd >= float(config.hard_drawdown) * 100`
  в одном месте и прямое сравнение `dd < tier["drawdown"]` в другом.
  Унифицировано.
- `Position.update_price` уже инкапсулирует расчёт PnL; risk engine теперь
  использует её, а не дублирует (и не падает на `side`).
- В `RiskEngine` добавлены публичные свойства `daily_pnl` / `weekly_pnl`
  для дашборда и ежедневного отчёта (раньше это были приватные поля).

### 3.3. Бэктестер: кумулятивный unrealized PnL

Метод `Backtester._update_equity_curve` делал:

```python
start_equity = self._equity
unrealized = sum(t.pnl for t in self._open_positions.values())
self._equity = self._equity + unrealized   # ❌
```

Поскольку `unrealized` уже содержит плавающий PnL *текущего* момента, на
каждом тике он прибавлялся снова, и капитал экспоненциально расходился в
зависимости от частоты тиков, а не от реальной доходности. Введено поле
`_realized_equity`, которое меняется только при закрытии позиции, а
`equity` на каждом тике вычисляется как `realized + unrealized`.
`start_equity` удалён.

### 3.4. OKX-адаптер

- `OKX_API_SANDBOX = "https://www.okx.com"` и WS sandbox указывал на
  прод-эндпоинт; для демо-счёта OKX требуется отдельный хост
  `wspap.okx.com` с `?brokerId=9999`. Поправлено в `client.py` и
  `websocket.py`.
- `get_orderbook` вызывал `data.get("bids", [])` у *списка* `data`
  (`_request` распаковывает поле `data` из ответа OKX), из-за чего стакан
  всегда возвращался пустым. Теперь берётся `data[0]` с проверкой типа.
- Фоновые `asyncio.create_task` в `OKXWebSocket` (handler сообщений,
  reconnect, on-candle/orderbook/trades подписки) и в
  `OKXOrderManager._on_order_closed` больше не теряются сборщиком мусора —
  они хранятся в `set` задач с автоматической очисткой через
  `add_done_callback`. `disconnect()` корректно отменяет их и ждёт
  завершения.

### 3.5. Web/FastAPI (`main.py`)

- `@app.on_event("startup")` заменён на современный `lifespan`, который
  корректно вызывает `bot.stop()` при остановке сервиса.
- Конфиг больше не падает с `RuntimeError: Settings not loaded`, если
  FastAPI поднят без предварительного `load_settings` (например, в
  Render/uwsgi).
- В `initialize` конфиг биржи брался как `settings.exchanges["okx"].get(...)`,
  хотя это `ExchangeConfig`, а не dict — падал `AttributeError` на старте.
- Все временные метки переведены на timezone-aware `datetime.now(timezone.utc)`.
- `PROJECT_ROOT` вычисляется как директория самого файла, а не
  `parent.parent` (который для корневого `main.py` указывал на `/home`).
- `render.yaml` теперь запускает `uvicorn main:app` (а также билдит и
  линтит тот же `main.py`), а Healthcheck в Dockerfile проверяет реальный
  HTTP `/health`, а не импорт пакета.

### 3.6. Конфигурация и переменные окружения

- `SystemConfig.from_yaml` не раскрывал плейсхолдеры `${VAR}` /
  `${VAR:-default}` из YAML, поэтому `password: "${DB_PASSWORD}"` так и
  оставалось литералом. Добавлен рекурсивный `_expand_env`, который
  применяется и к словарям, и к спискам.
- Загрузчик конфигурации поддерживал только секцию `system.instruments`
  и `system.strategies`, тогда как реальные `config/settings.yaml` и
  `production.yaml` раскладывают их в секцию `trading:`. Добавлен фолбэк
  на плоский layout.
- `RiskConfig.from_dict` обращался к `cls.drawdown_adaptation`, которого
  нет у датакласса с `default_factory` (падало `AttributeError`).
  Константа вынесена на уровень модуля.
- Удалён дублирующий каталог `astra_bot/config/` и ошибочный пакет
  `data/` (с `_init_.py`).

### 3.7. Качество кода, процессы и безопасность

- Добавлен `pyproject.toml` с единым конфигом для `ruff`, `black`,
  `mypy`, `pytest`. `pytest.ini` удалён.
- Включены разумные правила `ruff` (E/F/W/I/B/UP/SIM/PIE/RUF), отключены
  шумные (FURB, DTZ, BLE001 в адаптерах, RUF001/002/003 для кириллицы).
- `scikit-learn` и `lightgbm` переведены в опциональные: модуль
  `model_trainer.py` импортирует их лениво и поднимает понятный
  `ImportError` только при реальном вызове `train(...)`. `xgboost`
  остаётся опциональным (импортируется внутри метода).
- `requirements.txt` теперь включает `scikit-learn` и `lightgbm`, чтобы
  тесты и ML-слой работали из коробки; Dockerfile больше не доустанавливает
  их отдельным `pip install`.
- Добавлены `__init__.py` для `astra_bot.engines`,
  `astra_bot.paperengine`, `astra_bot.telegram` (пакеты импортировались
  только как namespace packages).
- Добавлен CI (`.github/workflows/ci.yml`) на Python 3.11/3.12 с
  `ruff check .` и `pytest --cov`, а также Dependabot для pip/docker/
  github-actions. *(Сам файл workflow не был запушен в ветку из-за
  ограничений GitHub-токена сессии на изменение `.github/workflows/` —
  он остаётся в рабочем дереве; чтобы включить CI, закоммитьте его
  отдельно с правами `workflows`.)*
- Добавлены интеграционные тесты FastAPI-приложения
  (`tests/integration/test_web_app.py`) на все эндпоинты.
- Все 17 настоящих ошибок класса F (unused imports, undefined names,
  переопределения имён) исправлены; автофиксами `ruff --fix` вычищены
  неиспользуемые импорты, сортировка, пробелы на пустых строках и т.п.

## 4. Зоны для дальнейшего улучшения (бэклог)

Это не блокирует запуск, но рекомендуется к планомерной работе:

1. **БД остаётся заглушкой.** `astra_bot/data/database.py` — это
   in-memory фейк без SQLAlchemy/asyncpg, хотя в `requirements.txt` и
   `init.sql` заявлена полноценная PostgreSQL-схема. Нужна реальная
   async-реализация `DatabaseManager` с пулом.
2. **Основной торговый цикл не реализован.** `astra_bot/main.py::_tick`
   содержит `TODO` и `pass`; web `main.py /tick` лишь обновляет equity,
   не получает котировки и не исполняет сигналы. Это следующий крупный
   этап разработки.
3. **Risk-движок принимает `side: str`**, тогда как доменная модель
   использует `models.Side`/`TradeDirection`. Стоит унифицировать типы и
   добавить валидацию.
4. **Telegram-бот не запускается вместе с веб-приложением.** Нужна
   интеграция с lifespan и ограничение доступа на уровне middleware
   (сейчас в `_is_allowed` фильтрация пользователей есть, но сам бот в
   web-моде не стартует).
5. **Prometheus-метрики не подключены.** `prometheus_client` в
   зависимостях есть, но `/metrics` эндпоинт не зарегистрирован;
   Grafana-дашборды лежат, но данных не получают.
6. **ML-модель:** `auto_train`, дрифт-детектор и предиктор описаны, но
   не подключены к прод-конвейеру; требуется хранение артефактов в
   S3/Blob storage и контроль версий.
7. **Лимиты OKX не реализованы.** `max_orders_per_minute: 30` из
   конфигурации нигде не применяются, нет rate-limiter-а на стороне
   клиента.
8. **Тесты покрывают в основном risk/paper/ML utilities** (119 тестов),
   но нет контрактных тестов OKXClient/WebSocket на записях фикстур.
   Рекомендуется добавить VCR/касcеты (`pytest-recording`/`respx`).
9. **Sensitive defaults в `.env.example`** выглядят как правдоподобные
   токены — стоит заменить на `your-...` плейсхолдеры и добавить
   валидацию, что значения в `.env` не совпадают с примером.
10. **Широкие `except Exception`** в адаптерах оставлены осознанно
    (вынесены в per-file-ignores), но для ядра стоило бы ввести
    типизированные исключения из `astra_bot.core.exceptions` и логировать
    `exc_info=True` везде, где это критично.

## 5. Как проверить

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

ruff check .
pytest -q

# Локальный запуск web-режима:
uvicorn main:app --host 0.0.0.0 --port 8000
curl http://127.0.0.1:8000/health
```

Ожидаемый результат:

- `ruff` — `All checks passed!`;
- `pytest` — `119 passed`;
- `/health` отдаёт `{"status": "healthy", ...}`.
