"""D5 фаза 0: HTF directional-фильтр — SHADOW (docs/HTF_DIRECTIONAL_FILTER_PLAN.md).

Решение владельца (12.09.2026): вход на младшем ТФ не должен идти против
направления старшего. До накопления выборки (план §3: 300+ сделок) фильтр
работает в SHADOW-режиме — входы НЕ блокирует, пишет гипотетические
запреты в ``models/htf_shadow_bans.jsonl``.

Правило (план §2.2, по образцу гейта multicurrency_mtf):
  * направление 4h по EMA20/50 на ЗАКРЫТЫХ барах (А5-семантика);
  * long запрещён, если EMA20 < EMA50 и close < EMA50; short — зеркально;
  * флип-стратегии (список имён из конфига) исключены: их сигнал и есть
    смена направления старшего ТФ (план §2.4);
  * fail-open: < ``min_closed_bars`` закрытых баров 4h или ошибка
    расчёта — фильтр молчит (приёмка «alpha fail-open», план §2.5).

Модуль только дописывает журнал (append-only, best-effort): любая ошибка
здесь НЕ влияет на торговое решение.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .indicators import ema

logger = logging.getLogger(__name__)

DEFAULT_SHADOW_PATH = "models/htf_shadow_bans.jsonl"
# Лимит ротации — как у no_trade_observations (core/state_rotation.py).
SHADOW_LOG_LIMIT = 5_000


def htf_bias(
    closed_closes: list[float],
    *,
    min_closed_bars: int = 60,
    fast: int = 20,
    slow: int = 50,
) -> tuple[str | None, dict[str, Any]]:
    """Направление старшего ТФ по закрытым барам.

    Возвращает ``(bias, diag)``:
      * ``"short"`` — рынок смотрит вниз: лонг гипотетически запрещён
        (EMA20 < EMA50 и последний закрытый close < EMA50);
      * ``"long"`` — зеркально: шорт гипотетически запрещён;
      * ``None`` — нейтрально ИЛИ fail-open (мало баров/ошибка ЕМА):
        отличать по ``diag["reason"]``.
    """
    diag: dict[str, Any] = {"bars": len(closed_closes), "min_bars": min_closed_bars}
    if len(closed_closes) < min_closed_bars:
        diag["reason"] = "fail_open_bars"
        return None, diag
    e_fast = ema(closed_closes, fast)
    e_slow = ema(closed_closes, slow)
    if e_fast is None or e_slow is None:
        diag["reason"] = "fail_open_ema"
        return None, diag
    last_close = float(closed_closes[-1])
    diag.update(
        ema_fast=round(e_fast, 10), ema_slow=round(e_slow, 10), close=last_close
    )
    if e_fast < e_slow and last_close < e_slow:
        return "short", diag
    if e_fast > e_slow and last_close > e_slow:
        return "long", diag
    diag["reason"] = "neutral"
    return None, diag


class HtfShadowLog:
    """Append-only журнал гипотетических запретов (фаза 0).

    Дедуп в рамках процесса: (символ, бар ТФ, стратегия, сторона) —
    один бар даёт не больше одной записи на пару стратегия+сторона.
    Запись best-effort: сбой — в лог, решение пайплайна не трогаем.
    """

    def __init__(self, path: str | Path = DEFAULT_SHADOW_PATH) -> None:
        self.path = Path(path)
        self._seen: set[tuple[str, int, str, str]] = set()

    def record(
        self,
        *,
        symbol: str,
        bar_time: int,
        strategy: str,
        direction: str,
        bias: str,
        ema_fast: float,
        ema_slow: float,
        close: float,
        entry_price: str,
        stop_loss: str,
        take_profit: str,
        rr: float,
        ts_ms: int | None = None,
    ) -> bool:
        """Записать один гипотетический запрет.

        Возвращает True, если строка дописана; False — дедуп или сбой.
        """
        key = (symbol, int(bar_time), strategy, direction)
        if key in self._seen:
            return False
        try:
            row = {
                "ts": int(ts_ms if ts_ms is not None else time.time() * 1000),
                "symbol": symbol,
                "bar_time": int(bar_time),
                "strategy": strategy,
                "direction": direction,
                "htf_bias": bias,
                "ema_fast": ema_fast,
                "ema_slow": ema_slow,
                "close4h": close,
                # Поля кандидата (решение владельца 12.09): чтобы после
                # накопления выборки мерить исходы запрещённых входов в R.
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "rr": round(float(rr), 4),
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._seen.add(key)
            return True
        except Exception as exc:
            logger.debug("htf_shadow write skipped: %s", exc)
            return False
