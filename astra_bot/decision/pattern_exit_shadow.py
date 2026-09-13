"""Фаза-0: тень паттерн-выходов (по образцу D5 ``htf_shadow.py``).

До накопления выборки паттерн-выход НЕ исполняется: на каждом баре для
каждой пережившей выходы позиции пишется, что БЫ случилось, — какой
разворотный паттерн против позиции виден и на каком R позиция стоит.
Журнал ``models/pattern_exit_shadow.jsonl`` — материал для калибровки
фазы-1 (бэклог, блок D): доля теневых выходов, средний исход после
тени и т.д.

Правило фазы-0 (по смыслу совпадает с confirm-паттернами
``strategies/multicurrency_mtf.py``, но самодостаточно и чисто):
  * лонг: медвежье поглощение или падающая звезда на последнем
    ЗАКРЫТОМ баре — гипотетический выход;
  * шорт — зеркально (бычье поглощение / молот);
  * А5-семантика: последний бар выборки — формирующийся, не смотрим
    (та же конвенция, что ``ExitPlanEngine._closed_bars``);
  * fail-open: < 2 закрытых баров или ошибка расчёта — молчим.

Модуль только дописывает журнал (append-only, best-effort): любая
ошибка здесь НЕ влияет на торговое решение.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PATTERN_EXIT_SHADOW_PATH = "models/pattern_exit_shadow.jsonl"
# Лимит ротации — как у htf_shadow_bans (core/state_rotation.py).
SHADOW_LOG_LIMIT = 5_000

_BEARISH = ("bearish_engulfing", "shooting_star")
_BULLISH = ("bullish_engulfing", "hammer")


def reversal_pattern(closed: list[Any]) -> str | None:
    """Разворотный паттерн на последнем ЗАКРЫТОМ баре (или None).

    Нужно минимум 2 закрытых бара (поглощение смотрит на предыдущий).
    Fail-open: мало баров, вырожденный диапазон, битые поля — None.
    """
    if len(closed) < 2:
        return None
    prev, cur = closed[-2], closed[-1]
    try:
        po = float(prev.open)
        pc = float(prev.close)
        co = float(cur.open)
        ch = float(cur.high)
        cl = float(cur.low)
        cc = float(cur.close)
    except (AttributeError, TypeError, ValueError):
        return None
    body = abs(cc - co)
    rng = ch - cl
    if not rng > 0:
        return None
    # Медвежье поглощение: prev бычий, cur медвежий и кроет его тело.
    if pc > po and cc < co and co >= pc and cc <= po:
        return "bearish_engulfing"
    # Падающая звезда: маленькое медвежье тело у низа, длинный верх.
    if (
        cc < co
        and body <= 0.3 * rng
        and (ch - max(co, cc)) >= 2 * body
        and (min(co, cc) - cl) <= body
    ):
        return "shooting_star"
    # Бычье поглощение — зеркально.
    if pc < po and cc > co and co <= pc and cc >= po:
        return "bullish_engulfing"
    # Молот — зеркально звезде.
    if (
        cc > co
        and body <= 0.3 * rng
        and (min(co, cc) - cl) >= 2 * body
        and (ch - max(co, cc)) <= body
    ):
        return "hammer"
    return None


def _r_now(direction: str, entry: float, risk: float, price: float) -> float | None:
    if not risk > 0:
        return None
    signed = (price - entry) / risk
    return round(signed if direction == "long" else -signed, 4)


class PatternExitShadow:
    """Append-only журнал гипотетических паттерн-выходов (фаза 0).

    Дедуп в рамках процесса: (символ, бар, позиция) — один бар даёт
    не больше одной записи на позицию. Запись best-effort: сбой —
    в лог, торговое решение не трогаем.
    """

    def __init__(self, path: str | Path = DEFAULT_PATTERN_EXIT_SHADOW_PATH) -> None:
        self.path = Path(path)
        self._seen: set[tuple[str, int, str]] = set()

    def observe_many(
        self,
        positions: list[Any],
        candles: list[Any],
        price: float,
        *,
        ts_ms: int | None = None,
    ) -> int:
        """Наблюдение переживших выходы позиций. Возвращает число строк."""
        n = 0
        for pos in positions:
            try:
                if self.observe(
                    position=pos, candles=candles, price=price, ts_ms=ts_ms
                ):
                    n += 1
            except Exception as exc:  # fail-open попозиционно
                logger.debug("pattern_exit_shadow skip %s: %s", pos, exc)
        return n

    def observe(
        self,
        *,
        position: Any,
        candles: list[Any],
        price: float,
        ts_ms: int | None = None,
    ) -> bool:
        """Записать одну строку тени. True — дописана; False — дедуп/сбой."""
        closed = list(candles[:-1])  # А5: последний бар формирующийся
        if len(closed) < 2:
            return False
        bar_time = int(getattr(closed[-1], "open_time", 0) or 0)
        key = (str(position.symbol), bar_time, str(position.id))
        if key in self._seen:
            return False
        try:
            pattern = reversal_pattern(closed)
            direction = str(position.direction)
            bearish = pattern in _BEARISH
            bullish = pattern in _BULLISH
            would_exit = (direction == "long" and bearish) or (
                direction == "short" and bullish
            )
            entry = float(position.entry_price)
            risk = float(position.risk_distance or 0)
            row = {
                "ts": int(ts_ms if ts_ms is not None else time.time() * 1000),
                "symbol": str(position.symbol),
                "position_id": str(position.id),
                "direction": direction,
                "strategy": str(position.strategy or ""),
                "bars_held": int(position.bars_held or 0),
                "bar_time": bar_time,
                "pattern": pattern,
                "would_exit": bool(would_exit),
                "price": float(price),
                "entry": entry,
                "stop": float(position.stop_loss),
                "risk_distance": risk,
                "r_now": _r_now(direction, entry, risk, float(price)),
                # Контекст для калибровки фазы-1: частичка уже снята?
                "partial_done": bool(
                    getattr(position, "tp_filled", None)
                    and any(position.tp_filled)
                ),
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._seen.add(key)
            return True
        except Exception as exc:
            logger.debug("pattern_exit_shadow write skipped: %s", exc)
            return False
