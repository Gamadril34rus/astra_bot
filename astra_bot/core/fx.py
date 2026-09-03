"""
Конвертация валют для отображения баланса в рублях.

Биржевой контур (BingX spot) не торгует RUB, поэтому курс USDT/RUB берём с бесплатного источника
(ЦБ РФ — курс доллара, т.к. USDT ≈ USD). Результат кэшируем на 1 час.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_CACHE: dict[str, Any] = {"usdrub": None, "ts": 0.0}
_TTL = 3600  # 1 час


def _fetch_cbr() -> float | None:
    """Курс USD/RUB с ЦБ РФ (ежедневный)."""
    try:
        req = urllib.request.Request(
            "https://www.cbr-xml-daily.ru/daily_json.js",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
            return float(data["Valute"]["USD"]["Value"])
    except Exception as exc:
        logger.debug("CBR fetch failed: %s", exc)
        return None


def _fetch_erapi() -> float | None:
    try:
        req = urllib.request.Request(
            "https://open.er-api.com/v6/latest/USD",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
            return float(data["rates"]["RUB"])
    except Exception as exc:
        logger.debug("er-api fetch failed: %s", exc)
        return None


def usd_to_rub(amount: float) -> float:
    """Перевести сумму в USD/USDT в рубли."""
    return float(amount) * get_usdrub()


def get_usdrub() -> float:
    """Курс USD/RUB с кэшем на час. Фолбэк ~83 если сеть недоступна."""
    now = time.time()
    if _CACHE["usdrub"] and (now - _CACHE["ts"]) < _TTL:
        return _CACHE["usdrub"]
    rate = _fetch_cbr() or _fetch_erapi() or 83.0
    _CACHE["usdrub"] = rate
    _CACHE["ts"] = now
    return rate
