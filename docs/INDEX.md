# ASTRA BOT — карта документации

Этот файл является входной точкой в документацию проекта. Документы разделены по назначению, чтобы техническая информация, эксплуатация и торговая логика не смешивались в одну огромную папку человеческого отчаяния.

## 1. Архитектура

| Документ | Назначение |
|---|---|
| `ARCHITECTURE.md` | Полная архитектура системы и связи компонентов |
| `DECISION_PIPELINE.md` | Как формируется торговое решение |
| `STRATEGY_SPEC.md` | Спецификация стратегий |
| `strategy_portfolio.md` | Портфель стратегий «трендовая книга», walk-forward и мультивалютный MTF-аудит |
| `PULLBACK_STRATEGY.md` | Детали pullback-стратегии |
| `RISK_INTEGRATION.md` | Risk Engine в живом paper-контуре: лимиты, HALT, persistence, издержки |
| `META_STRATEGY.md` | Meta-Strategy: выбор по EV в режиме, shrinkage, NO_TRADE-память |

## 2. Обучение и память

| Документ | Назначение |
|---|---|
| `SELF_PLAY.md` | Историческая виртуальная торговля и self-play |
| `PROFIT_AND_TRAINING.md` | Методика обучения и оценки результата |
| `PROJECT_REVIEW.md` | Технический аудит и известные ограничения |

## 3. Риск

| Документ | Назначение |
|---|---|
| `RISK_AND_GOALS.md` | Цели, ограничения и риск-политика |
| `ARCHITECTURE.md` | Архитектурные safety-механизмы |

## 4. Эксплуатация

| Документ | Назначение |
|---|---|
| `GITHUB_ACTIONS.md` | Работа workflow в GitHub Actions |
| `LOCAL_SETUP.md` | Локальная установка и запуск |
| `DIAGNOSTICS.md` | Диагностика типовых проблем |
| `../deploy/INSTALL_DEMO.md` | Установка на VPS/systemd |

## 5. Быстрый маршрут

### Разработчик

`LOCAL_SETUP.md` → `ARCHITECTURE.md` → `DECISION_PIPELINE.md` → тесты.

### Оператор Demo

`GITHUB_ACTIONS.md` → `DIAGNOSTICS.md` → `RISK_AND_GOALS.md`.

### Анализ обучения

`SELF_PLAY.md` → `PROFIT_AND_TRAINING.md` → `PROJECT_REVIEW.md`.

### Переезд на VPS

`LOCAL_SETUP.md` → `../deploy/INSTALL_DEMO.md`.

## 6. Правило документации

Новые документы добавляются только если существующие нельзя логично расширить. README содержит краткую картину проекта; `docs/` содержит детали. Секреты, реальные ключи и персональные данные никогда не помещаются в документацию.
