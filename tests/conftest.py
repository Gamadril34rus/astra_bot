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
    # Симулятор и непрерывный цикл — только там, где тест включил их
    # явно (os.environ.setdefault в тест-файлах протекает между ними).
    monkeypatch.delenv("ASTRA_SIMULATE", raising=False)
    monkeypatch.delenv("ASTRA_CONTINUOUS", raising=False)
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


@pytest.fixture(autouse=True)
def _isolate_symbol_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A5: state per-symbol guard — в tmp, не в models/ репозитория.

    Путь по умолчанию в TradingEngineConfig CWD-относительный; фикс A5
    сделал guard персистентным, и без изоляции счётчики протекают между
    тестами одной сессии (SYMBOL_COOLDOWN на первом тике реплей-тестов).
    ВАЖНО: setattr на классе не работает — dataclass печатает дефолт
    в __init__, инстанс перекрывает атрибут класса.
    """
    from astra_bot.decision.trading_engine import TradingEngineConfig

    guard_path = str(tmp_path / "symbol_loss_guard.json")
    monkeypatch.setattr(TradingEngineConfig, "symbol_loss_guard_path", guard_path)
    orig_init = TradingEngineConfig.__init__

    def _init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        self.symbol_loss_guard_path = guard_path

    monkeypatch.setattr(TradingEngineConfig, "__init__", _init)
    yield
