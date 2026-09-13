"""
Бюджет торговых часов на месяц.

Идея владельца: «боту разрешено торговать ограниченное число часов в
месяц». Бюджет покрывает круглосуточную работу (дефолт 750 ч/мес,
переопределяется через TRADE_HOURS_PER_MONTH). В остатке — защита от
овертрейдинга: при исчерпании бюджета новые позиции не открываются.

Реализация:
* бюджет хранится в персистентном файле ``models/trading_budget.json``;
* в начале месяца счётчик сбрасывается;
* ``can_trade_now(now)`` говорит, активен ли бот в данный момент
  (все часы суток по умолчанию — решение владельца 13.09.2026);
* ``record_hour()`` / ``record_minutes()`` учитывают фактически
  наторгованное время;
* Telegram-команды показывают остаток.

Расписание внутри дня — 24/7: bot.yml уже круглосуточный, в paper
проскальзывание модельное (0.1% на сторону), а данные 24/7 ценнее
пропущенных ночных движений. Сузить окно можно через
TRADE_ACTIVE_HOURS_MSK (например "8-23").
"""

from __future__ import annotations

import calendar
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE_PATH = Path(__file__).resolve().parents[2] / "models" / "trading_budget.json"

# Сколько часов в месяц разрешено торговать по умолчанию. 750 часов:
# длинный месяц (31 день) — это 744 часа, бюджет покрывает 24/7 целиком
# и бот не встанет в конце месяца. Можно переопределить через
# TRADE_HOURS_PER_MONTH.
DEFAULT_HOURS_PER_MONTH = 750

MSK = timezone(timedelta(hours=3))


@dataclass
class BudgetState:
    year: int
    month: int
    budget_hours: float
    used_minutes: float = 0.0
    last_date: str = ""          # последний день, за который учитывали часы
    last_minute_checked: str = ""
    # Сколько минут использовано В ПРЕДЫДУЩИЕ дни текущего месяца
    # (для расчёта дневного остатка). Внутри сегодняшнего дня копится
    # отдельно, чтобы при смене суток перенестись.
    used_minutes_before_today: float = 0.0
    used_minutes_today: float = 0.0

    @property
    def used_hours(self) -> float:
        return self.used_minutes / 60.0

    @property
    def remaining_hours(self) -> float:
        return max(0.0, self.budget_hours - self.used_hours)

    @property
    def remaining_minutes(self) -> float:
        return max(0.0, self.budget_hours * 60 - self.used_minutes)

    def to_dict(self) -> dict:
        return asdict(self)


# Активные часы торговли по МСК. Дефолт — ВСЕ сутки 0–23 (решение
# владельца 13.09.2026, 24/7): bot.yml уже круглосуточный, в paper
# проскальзывание модельное (0.1% на сторону), данные 24/7 ценнее
# пропущенных ночных движений. Прежняя мотивировка «ночь — тонкий
# рынок» для paper-контура снята.
# Сузить окно: TRADE_ACTIVE_HOURS_MSK="8-23" (диапазон) или "0,1,2"
# (перечисление); часы через запятую и диапазоны можно комбинировать.
ACTIVE_HOURS_MSK: frozenset[int] = frozenset(range(24))  # 00:00–24:00 МСК


def _active_hours() -> frozenset[int]:
    """Активные часы с учётом TRADE_ACTIVE_HOURS_MSK (дефолт — 24/7)."""
    raw = (os.environ.get("TRADE_ACTIVE_HOURS_MSK") or "").strip()
    if not raw:
        return ACTIVE_HOURS_MSK
    try:
        hours: set[int] = set()
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start_s, end_s = part.split("-", 1)
                hours.update(range(int(start_s), int(end_s) + 1))
            else:
                hours.add(int(part))
        hours = {h for h in hours if 0 <= h <= 23}
        if hours:
            return frozenset(hours)
    except ValueError:
        pass
    logger.warning(
        "TRADE_ACTIVE_HOURS_MSK=%r не распознан — использую 24/7", raw
    )
    return ACTIVE_HOURS_MSK


def _state_path() -> Path:
    return Path(os.environ.get("TRADING_BUDGET_FILE", str(_STATE_PATH)))


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _load_or_reset(now: datetime) -> BudgetState:
    """Прочитать состояние; при смене месяца — сбросить бюджет."""
    path = _state_path()
    budget = float(os.environ.get("TRADE_HOURS_PER_MONTH", DEFAULT_HOURS_PER_MONTH))

    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            state = BudgetState(**{k: v for k, v in data.items()
                                   if k in BudgetState.__dataclass_fields__})
            # Сброс по новому месяцу.
            if state.year != now.year or state.month != now.month:
                logger.info("Новый месяц %04d-%02d — сброс бюджета часов",
                            now.year, now.month)
                state = BudgetState(
                    year=now.year, month=now.month, budget_hours=budget
                )
            # Смена суток: переносим сегодняшний счётчик в «до сегодня».
            today = now.date().isoformat()
            if state.last_date and state.last_date != today:
                state.used_minutes_before_today += state.used_minutes_today
                state.used_minutes_today = 0.0
            # Подхватываем изменение бюджета из окружения.
            if abs(state.budget_hours - budget) > 1e-6:
                state.budget_hours = budget
            return state
        except Exception as exc:
            logger.warning("Не смог прочитать бюджет часов: %s", exc)

    return BudgetState(
        year=now.year, month=now.month, budget_hours=budget
    )


def _save(state: BudgetState) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)


def _now_msk() -> datetime:
    return datetime.now(tz=MSK)


def hours_per_day(now: datetime | None = None) -> float:
    """Сколько часов в сутки доступно в текущем месяце (бюджет / дни месяца)."""
    now = now or _now_msk()
    return DEFAULT_HOURS_PER_MONTH / _days_in_month(now.year, now.month) \
        if not os.environ.get("TRADE_HOURS_PER_MONTH") \
        else float(os.environ["TRADE_HOURS_PER_MONTH"]) / _days_in_month(now.year, now.month)


def remaining_minutes_today(now: datetime | None = None) -> float:
    """Остаток минут на сегодня (бюджет дня минус уже потраченное сегодня)."""
    now = now or _now_msk()
    state = _load_or_reset(now)
    return max(0.0, hours_per_day(now) * 60 - state.used_minutes_today)


def can_trade_now(now: datetime | None = None) -> bool:
    """Можно ли торговать прямо сейчас.

    Условия (все должны выполняться):
    1. Сейчас активные часы суток (дефолт 24/7; см. _active_hours).
    2. Не исчерпан дневной лимит минут.
    3. Не исчерпан месячный бюджет.
    """
    now = now or _now_msk()
    if now.hour not in _active_hours():
        return False

    state = _load_or_reset(now)
    if state.remaining_minutes <= 0:
        return False

    daily_left = remaining_minutes_today(now)
    return daily_left > 0


def record_minutes(minutes: float, now: datetime | None = None) -> float:
    """Учесть фактически наторгованные минуты. Возвращает остаток месяца (ч)."""
    now = now or _now_msk()
    state = _load_or_reset(now)
    today = now.date().isoformat()
    if state.last_date != today:
        # Смена суток: переносим сегодняшний счётчик в «до сегодня».
        state.used_minutes_before_today += state.used_minutes_today
        state.used_minutes_today = 0.0
    inc = max(0.0, float(minutes))
    state.used_minutes_today += inc
    state.used_minutes = min(
        state.budget_hours * 60,
        state.used_minutes_before_today + state.used_minutes_today,
    )
    state.last_date = today
    state.last_minute_checked = now.strftime("%H:%M")
    _save(state)
    return state.remaining_hours


def tick(now: datetime | None = None) -> bool:
    """Вызывать раз в минуту. Учитывает минуту, если торги разрешены.

    Возвращает can_trade_now().
    """
    now = now or _now_msk()
    allowed = can_trade_now(now)
    if allowed:
        record_minutes(1.0, now)
    return allowed


def get_status(now: datetime | None = None) -> dict:
    """Сводка для Telegram."""
    now = now or _now_msk()
    state = _load_or_reset(now)
    dim = _days_in_month(now.year, now.month)
    day_budget_h = state.budget_hours / dim
    hrs = _active_hours()
    return {
        "now_msk": now.strftime("%d.%m.%Y %H:%M"),
        "month": f"{now.year}-{now.month:02d}",
        "days_in_month": dim,
        "budget_hours": state.budget_hours,
        "used_hours": round(state.used_hours, 2),
        "remaining_hours": round(state.remaining_hours, 2),
        "hours_per_day": round(day_budget_h, 2),
        "daily_remaining_minutes": round(remaining_minutes_today(now), 1),
        "active_hours_msk": f"{min(hrs):02d}:00–{max(hrs)+1:02d}:00",
        "can_trade_now": can_trade_now(now),
    }


def set_budget(hours: float, now: datetime | None = None) -> dict:
    """Изменить месячный бюджет часов (из Telegram)."""
    now = now or _now_msk()
    state = _load_or_reset(now)
    state.budget_hours = max(1.0, float(hours))
    _save(state)
    return get_status(now)
