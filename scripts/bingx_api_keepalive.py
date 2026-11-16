#!/usr/bin/env python3
"""ASTRA — daily BingX API keep-alive (read-only balance ping).

Зачем:
  Ключ BingX без IP-вайтлиста и с торговыми/приватными правами биржа
  удаляет после 14 дней без вызовов API. Этот скрипт раз в сутки делает
  подписанный запрос баланса (VST по умолчанию) и опционально шлёт
  краткий отчёт владельцу в Telegram — ключ остаётся «живым», демо-контур
  видит актуальный equity.

Никаких ордеров. Только GET balance + positions count.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# repo root on PYTHONPATH
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("bingx_keepalive")


def _tg_send(text: str) -> None:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_ADMIN_ID") or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        log.info("Telegram secrets missing — skip notify")
        return
    import urllib.parse
    import urllib.request

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode(
        {"chat_id": chat, "text": text, "disable_web_page_preview": "1"}
    ).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()
    log.info("Telegram notify sent")


async def _run() -> int:
    from astra_bot.adapters.bingx.client import BingXClient
    from astra_bot.adapters.bingx.live_env import resolve_base_url

    key = (os.environ.get("BINGX_API_KEY") or "").strip()
    secret = (os.environ.get("BINGX_API_SECRET") or "").strip()
    if not key or not secret:
        log.error("BINGX_API_KEY / BINGX_API_SECRET not set")
        return 2

    base = resolve_base_url()
    log.info("base_url=%s (VST default unless ZEUS_MAINNET=1)", base)

    client = BingXClient(
        {
            "api_key": key,
            "api_secret": secret,
            "base_url": base,
            "enabled": True,
        }
    )
    await client.initialize()
    lines: list[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"ASTRA keep-alive · {now}")
    lines.append(f"API host: {base.replace('https://', '')}")

    equity = None
    try:
        if hasattr(client, "get_balance_usdt"):
            equity = await client.get_balance_usdt()
            lines.append(f"Equity USDT: {equity}")
            log.info("get_balance_usdt -> %s", equity)
        elif hasattr(client, "get_account_balance"):
            bals = await client.get_account_balance()
            lines.append(f"Balances: { {k: str(v.total) for k, v in bals.items()} }")
            log.info("get_account_balance -> %s", bals)
        else:
            # raw fallback
            resp = await client._request(
                "GET",
                "/openApi/swap/v2/user/balance",
                params={},
                signed=True,
            )
            lines.append(f"balance raw code={resp.get('code')}")
            log.info("raw balance: %s", json.dumps(resp, default=str)[:1500])
    except Exception as exc:
        log.exception("balance ping failed")
        lines.append(f"Balance ERROR: {type(exc).__name__}: {exc}")
        await client.close()
        _tg_send("\n".join(lines))
        return 1

    try:
        if hasattr(client, "get_positions"):
            pos = await client.get_positions()
            lines.append(f"Open positions: {len(pos)}")
            log.info("positions n=%s", len(pos))
    except Exception as exc:
        log.warning("positions failed: %s", type(exc).__name__)
        lines.append(f"Positions: n/a ({type(exc).__name__})")

    await client.close()
    lines.append("Key activity: OK (14-day idle timer reset)")
    text = "\n".join(lines)
    print(text)
    _tg_send(text)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
