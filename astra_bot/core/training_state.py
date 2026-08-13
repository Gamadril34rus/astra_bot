"""
Сохраняемый «капитал обучения» между запусками self-play.

После каждого прогона ``final_equity`` записывается в JSON, и при следующем
запуске он же используется как ``initial_capital``. За счёт этого виртуальный
счёт обучения растёт после прибыльных серий и уменьшается после убыточных,
а не сбрасывается каждый раз на 2000 ₽.

Дополнительно хранятся:
* флаг запроса на остановку обучения (``stop_requested``) — команда
  «Прекратить обучение» из Telegram выставляет его, а цикл self-play
  проверяет через :meth:`TrainingState.should_stop`;
* настройки времени оповещений (``daily_report_time``, ``quiet_hours``,
  ``alerts_enabled``), которые можно менять командой из Telegram;
* накопленная статистика wins/losses/total_pnl по всем запускам.

Файл лежит в ``models/training_state.json``. ``models/`` в .gitignore,
поэтому состояние не попадает в репозиторий и переживает перезапуск
процесса/контейнера (при persistence диска).
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Абсолютный путь к файлу состояния. Можно переопределить переменной
# окружения TRAINING_STATE_FILE (на случай если models/ смонтирован не туда).
_STATE_PATH = Path(__file__).resolve().parents[2] / "models" / "training_state.json"

# Стартовый капитал при самом первом запуске.
DEFAULT_INITIAL_CAPITAL = Decimal("2000")
# Ниже этого порога не опускаемся — даже после серии убытков новая сессия
# стартует с MIN_CAPITAL, чтобы не торговать «на ноль» и не словить
# деление на ноль в расчёте размера позиции.
MIN_CAPITAL = Decimal("500")
# Сверху капитал обучения тоже ограничен — чтобы одна сверхудачная серия
# не раздула ставки до бесконечности. Можно изменить через переменную
# окружения TRAINING_MAX_CAPITAL.
DEFAULT_MAX_CAPITAL = Decimal("20000")

_LOCK = threading.Lock()


@dataclass
class RunStats:
    """Совокупная статистика по всем учебным сессиям."""

    runs: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    best_equity: float = 0.0
    worst_equity: float = 0.0


@dataclass
class TrainingState:
    """Персистентное состояние обучения и оповещений."""

    # Капитал, с которого начнётся СЛЕДУЮЩАЯ сессия self-play.
    initial_capital: str = "2000.00"
    # Капитал на момент последнего завершения (для истории/отчётов).
    last_final_equity: str = "2000.00"
    last_run_at: str | None = None
    last_run_trades: int = 0
    last_run_pnl: float = 0.0

    # Запрос на остановку: выставляется командой «Прекратить обучение».
    stop_requested: bool = False
    # Идентификатор запущенной сессии (uuid), чтобы отличать устаревшие
    # запросы на остановку.
    session_id: str | None = None

    # Настройки оповещений.
    daily_report_time: str = "09:00"   # МСК
    alerts_enabled: bool = True
    # Тихие часы: алерты о сделках не отправляются в этот интервал МСК.
    # Утренний отчёт и критические алерты игнорируют тихие часы.
    quiet_hours_start: str | None = None  # например "23:00"
    quiet_hours_end: str | None = None    # например "08:00"

    # Дата последнего стартового сообщения «бот на связи» (анти-спам при
    # частых перезапусках на GitHub Actions).
    last_startup_message: str | None = None

    # Накопленная статистика.
    stats: RunStats = field(default_factory=RunStats)

    # ---------- фабрики/сохранение ----------
    @classmethod
    def path(cls) -> Path:
        import os
        return Path(os.environ.get("TRAINING_STATE_FILE", str(_STATE_PATH)))

    @classmethod
    def load(cls) -> "TrainingState":
        """Прочитать состояние с диска; при отсутствии/ошибке — дефолты."""
        path = cls.path()
        if not path.exists():
            state = cls()
            state.initial_capital = str(DEFAULT_INITIAL_CAPITAL)
            state.last_final_equity = str(DEFAULT_INITIAL_CAPITAL)
            state.stats.best_equity = float(DEFAULT_INITIAL_CAPITAL)
            state.stats.worst_equity = float(DEFAULT_INITIAL_CAPITAL)
            return state
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Не удалось прочитать %s: %s — начинаю с дефолтов", path, exc)
            return cls()

        stats_data = data.pop("stats", {}) or {}
        state = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        state.stats = RunStats(**{
            k: v for k, v in stats_data.items() if k in RunStats.__dataclass_fields__
        })
        # Подстраховка от битого/нулевого капитала.
        try:
            cap = Decimal(state.initial_capital)
            if cap <= 0:
                raise InvalidOperation
        except (InvalidOperation, TypeError):
            state.initial_capital = str(DEFAULT_INITIAL_CAPITAL)
        return state

    def save(self) -> None:
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    # ---------- капитал ----------
    def get_initial_capital(self) -> Decimal:
        """Капитал для следующей сессии, зажатый в [MIN_CAPITAL, MAX_CAPITAL]."""
        import os
        try:
            cap = Decimal(self.initial_capital)
        except (InvalidOperation, TypeError):
            cap = DEFAULT_INITIAL_CAPITAL
        max_cap = Decimal(os.environ.get("TRAINING_MAX_CAPITAL", str(DEFAULT_MAX_CAPITAL)))
        if cap < MIN_CAPITAL:
            cap = MIN_CAPITAL
        if cap > max_cap:
            cap = max_cap
        return cap

    def record_run(
        self,
        final_equity: Decimal | float,
        trades: int,
        wins: int,
        losses: int,
        pnl: Decimal | float,
    ) -> Decimal:
        """Зафиксировать результат завершившейся сессии.

        Возвращает капитал, который будет использован при следующем запуске
        (после клампа в [MIN, MAX]).
        """
        final = Decimal(str(final_equity))
        next_cap = self.get_initial_capital_from_final(final)
        self.last_final_equity = str(final)
        self.initial_capital = str(next_cap)
        self.last_run_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        self.last_run_trades = int(trades)
        self.last_run_pnl = float(pnl)
        self.session_id = None
        self.stop_requested = False

        self.stats.runs += 1
        self.stats.total_trades += int(trades)
        self.stats.wins += int(wins)
        self.stats.losses += int(losses)
        self.stats.total_pnl += float(pnl)
        f = float(final)
        if self.stats.best_equity == 0.0 or f > self.stats.best_equity:
            self.stats.best_equity = f
        if self.stats.worst_equity == 0.0 or f < self.stats.worst_equity:
            self.stats.worst_equity = f

        self.save()
        return next_cap

    @staticmethod
    def get_initial_capital_from_final(final_equity: Decimal) -> Decimal:
        """Следующий стартовый капитал = текущий капитал, с защитой от
        слива и от раздутия ставок.

        * Если серия в плюсе — следующий старт с текущей суммы (compounding).
        * Если в просадке — стартуем с того, что осталось, но не меньше
          MIN_CAPITAL (страховка, чтобы счёт «не обнулился»).
        * Сверху ограничено MAX_CAPITAL.
        """
        import os
        max_cap = Decimal(os.environ.get("TRAINING_MAX_CAPITAL", str(DEFAULT_MAX_CAPITAL)))
        cap = Decimal(str(final_equity))
        if cap < MIN_CAPITAL:
            cap = MIN_CAPITAL
        if cap > max_cap:
            cap = max_cap
        return cap

    def reset_capital(self, value: Decimal | float | str | None = None) -> Decimal:
        """Сбросить капитал обучения (команда /сброскапитал)."""
        cap = DEFAULT_INITIAL_CAPITAL if value is None else Decimal(str(value))
        if cap < MIN_CAPITAL:
            cap = MIN_CAPITAL
        self.initial_capital = str(cap)
        self.last_final_equity = str(cap)
        self.save()
        return cap

    # ---------- остановка ----------
    def request_stop(self) -> None:
        self.stop_requested = True
        self.save()

    def clear_stop(self) -> None:
        self.stop_requested = False
        self.save()

    def should_stop(self) -> bool:
        # Перечитываем флаг с диска, чтобы команда «стоп» из другого
        # процесса/воркера тоже срабатывала.
        fresh = TrainingState.load()
        return fresh.stop_requested

    def start_session(self, session_id: str) -> None:
        self.session_id = session_id
        self.stop_requested = False
        self.save()

    # ---------- оповещения ----------
    def set_daily_report_time(self, hhmm: str) -> str:
        self._validate_hhmm(hhmm)
        self.daily_report_time = hhmm
        self.save()
        return self.daily_report_time

    def set_alerts(self, enabled: bool) -> bool:
        self.alerts_enabled = bool(enabled)
        self.save()
        return self.alerts_enabled

    def set_quiet_hours(self, start: str | None, end: str | None) -> tuple[str | None, str | None]:
        if (start is None) != (end is None):
            raise ValueError("Тихие часы задаются парой: начало и конец")
        if start and end:
            self._validate_hhmm(start)
            self._validate_hhmm(end)
        self.quiet_hours_start = start
        self.quiet_hours_end = end
        self.save()
        return self.quiet_hours_start, self.quiet_hours_end

    def in_quiet_hours(self, now: datetime | None = None) -> bool:
        """Сейчас тихие часы по МСК? (Утренний отчёт это не блокирует.)"""
        if not (self.quiet_hours_start and self.quiet_hours_end):
            return False
        now = now or datetime.now()
        t = now.strftime("%H:%M")
        s, e = self.quiet_hours_start, self.quiet_hours_end
        if s <= e:  # один и тот же день, напр. 23:00–23:59
            return s <= t < e
        # Переход через полночь, напр. 23:00–08:00
        return t >= s or t < e

    @staticmethod
    def _validate_hhmm(hhmm: str) -> None:
        # Строгий формат ЧЧ:ММ с ведущим нулём и часом 00–23.
        try:
            parts = hhmm.split(":")
            if len(parts) != 2 or len(parts[0]) != 2 or len(parts[1]) != 2:
                raise ValueError
            hh, mm = int(parts[0]), int(parts[1])
            if not (0 <= hh <= 23 and 0 <= mm <= 59):
                raise ValueError
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError(f"Нужен формат ЧЧ:ММ (00:00–23:59), получено: {hhmm!r}") from exc


# Глобальный синглтон с ленивой загрузкой и блокировкой.
_STATE: TrainingState | None = None


def get_training_state() -> TrainingState:
    global _STATE
    with _LOCK:
        if _STATE is None:
            _STATE = TrainingState.load()
        return _STATE


def reload_training_state() -> TrainingState:
    """Перечитать состояние с диска (после ручного изменения)."""
    global _STATE
    with _LOCK:
        _STATE = TrainingState.load()
        return _STATE
