#!/usr/bin/env python3
"""Zeus LIVE diagnostic tick — баланс, только чтение (ордеров нет).

Симптом: запуск #3 зелёный, reconcile даёт equity=0, а скрин владельца
показывает демо-кошелёк USDM ≈100 000 VST — клиент читает не тот
под-кошелёк/поле. Скрипт печатает сырые ответы v3/v2, разобранный
баланс и число позиций, чтобы увидеть, где именно теряются деньги.

Скрипт ТОЛЬКО читает: ни place_order, ни cancel, ни close. Mainnet
недостижим — guard держит VST (ZEUS_VST=1 по умолчанию).

Запуск: PYTHONPATH=. python scripts/zeus_live_debug_tick.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("zeus_live_debug")


def _guard() -> None:
    env = (os.environ.get("ENVIRONMENT") or "").strip().lower()
    live = (os.environ.get("ZEUS_LIVE") or "").strip()
    if env == "live" and live != "1":
        logger.error("ABORT: ENVIRONMENT=live requires ZEUS_LIVE=1")
        sys.exit(2)
    if live != "1":
        logger.error("ABORT: ZEUS_LIVE!=1 — live clock inactive")
        sys.exit(2)
    if env != "live":
        logger.error("ABORT: ZEUS_LIVE=1 requires ENVIRONMENT=live (got %r)", env)
        sys.exit(2)
    mainnet = (os.environ.get("ZEUS_MAINNET") or "").strip()
    vst = (os.environ.get("ZEUS_VST") or "1").strip()
    if mainnet == "1" and vst in ("1", "true", "yes"):
        logger.error("ABORT: ZEUS_MAINNET=1 conflicts with ZEUS_VST=1")
        sys.exit(2)
    if mainnet == "1":
        logger.warning("MAINNET mode — real money path enabled by owner flag")
    else:
        logger.info("VST mode (demo futures) — default")


async def debug_tick() -> int:
    """Прогнать пять read-only замеров баланса подряд."""
    from astra_bot.adapters.bingx.client_live import LiveBingXClient

    client = LiveBingXClient({})
    await client.initialize()
    ts = int(time.time() * 1000)
    try:
        raw = await client._request(
            "GET", "/openApi/swap/v3/user/balance",
            params={"timestamp": ts}, signed=True,
        )
        logger.info("v3 balance raw: %s", raw)
    except Exception as exc:
        logger.warning("v3 balance raw failed: %s", type(exc).__name__)
    try:
        raw2 = await client._request(
            "GET", "/openApi/swap/v2/user/balance",
            params={"timestamp": ts}, signed=True,
        )
        logger.info("v2 balance raw: %s", raw2)
    except Exception as exc:
        logger.warning("v2 balance raw failed: %s", type(exc).__name__)
    try:
        bals = await client.get_account_balance()
        logger.info("parsed v2: %s", {k: str(v.total) for k, v in bals.items()})
    except Exception as exc:
        logger.warning("parsed v2 failed: %s", type(exc).__name__)
    try:
        eq = await client.get_balance_usdt()
        logger.info("get_balance_usdt -> %s", eq)
    except Exception as exc:
        logger.warning("get_balance_usdt failed: %s", type(exc).__name__)
    try:
        pos = await client.get_positions()
        logger.info("positions: %s", len(pos))
    except Exception as exc:
        logger.warning("positions failed: %s", type(exc).__name__)
    session = getattr(client, "_session", None)
    if session is not None:
        await session.close()
    return 0


def main() -> None:
    _guard()
    raise SystemExit(asyncio.run(debug_tick()))


if __name__ == "__main__":
    main()
