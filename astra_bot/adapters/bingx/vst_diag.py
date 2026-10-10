"""Zeus VST balance diagnostics — read-only, no orders.

Диагностика симптома: reconcile отдаёт equity=0, а демо-кошелёк USDM
у владельца непустой (~100 000 VST по скриншоту) — клиент читает баланс
не из того под-кошелька/поля VST API.

Вызывается из scripts/zeus_live_clock.py, когда рядом лежит файл-флаг
models/zeus_live_debug.json. Все строки помечены префиксом VSTDIAG,
чтобы их было видно в логе запуска. Скрипт только читает: ни одного
place_order / cancel / close.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

RAW_LIMIT = 2000


async def dump(client: Any, logger: logging.Logger) -> None:
    """Пять read-only замеров баланса подряд, каждый в своём try/except.

    Один сбой не должен рвать остальные — поэтому на шаг try/except
    и логируется только тип исключения.
    """
    ts = int(time.time() * 1000)
    try:
        resp = await client._request(
            "GET",
            "/openApi/swap/v3/user/balance",
            params={"timestamp": ts},
            signed=True,
        )
        logger.info("VSTDIAG v3 raw: %s", json.dumps(resp, default=str)[:RAW_LIMIT])
    except Exception as exc:
        logger.warning("VSTDIAG v3 raw failed: %s", type(exc).__name__)

    try:
        resp = await client._request(
            "GET",
            "/openApi/swap/v2/user/balance",
            params={"timestamp": ts},
            signed=True,
        )
        logger.info("VSTDIAG v2 raw: %s", json.dumps(resp, default=str)[:RAW_LIMIT])
    except Exception as exc:
        logger.warning("VSTDIAG v2 raw failed: %s", type(exc).__name__)

    try:
        bals = await client.get_account_balance()
        logger.info("VSTDIAG v2 parsed: %s", {k: str(v.total) for k, v in bals.items()})
    except Exception as exc:
        logger.warning("VSTDIAG v2 parsed failed: %s", type(exc).__name__)

    try:
        eq = await client.get_balance_usdt()
        logger.info("VSTDIAG get_balance_usdt -> %s", eq)
    except Exception as exc:
        logger.warning("VSTDIAG get_balance_usdt failed: %s", type(exc).__name__)

    try:
        pos = await client.get_positions()
        logger.info("VSTDIAG positions: %d", len(pos))
    except Exception as exc:
        logger.warning("VSTDIAG positions failed: %s", type(exc).__name__)
