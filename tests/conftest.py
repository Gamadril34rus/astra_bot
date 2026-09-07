"""Общие фикстуры тестового контура.

Тесты обязаны быть hermetic: никакой записи в продакшн-файлы репо
(data/, models/, logs/). Раньше прогоны pytest мутировали закоммиченные
data/state.json, data/trades.db, models/strategy_stats.json и
logs/errors.log — утренний отчёт считал фейковые сделки и ошибки.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Изолировать git-based persistence от продакшн-каталогов.

    StateManager уважает ASTRA_STATE_DIR (и пересоздаёт синглтон при
    смене окружения), поэтому достаточно выставить переменную и сбросить
    кэш-менеджер перед каждым тестом.
    """
    state_dir = tmp_path / "astra-state"
    monkeypatch.setenv("ASTRA_STATE_DIR", str(state_dir))
    from astra_bot.data import state_manager

    state_manager.reset_state_manager()
    yield
    state_manager.reset_state_manager()


@pytest.fixture(autouse=True)
def _isolate_error_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Под pytest retry-лог ошибок уходит в tmp, а не в logs/errors.log.

    _log_error_to_file дополнительно защищён проверкой PYTEST_CURRENT_TEST;
    переменная ниже — страховка для запусков вне pytest-раннера.
    """
    monkeypatch.setenv("ASTRA_ERROR_LOG", str(tmp_path / "errors.log"))
    yield
