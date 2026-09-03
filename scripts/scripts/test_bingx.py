"""Проверка соединения с BingX и валидности API-ключей без вывода секретов."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv()
from astra_bot.adapters.bingx import BingXClient


async def main() -> int:
    key = os.environ.get("BINGX_API_KEY", "")
    secret = os.environ.get("BINGX_API_SECRET", "")

    cfg = {"enabled": True}
    if key and secret:
        cfg.update({"api_key": key, "api_secret": secret})
    else:
        print("BINGX_API_KEY/BINGX_API_SECRET не заданы — проверю только "
              "публичные рыночные данные (этого достаточно для paper-контура).")

    c = BingXClient(cfg)
    await c.initialize()
    try:
        candles = await c.get_candles("BTC-USDT", timeframe="1h", limit=5)
        if not candles:
            print("Public BingX endpoint failed")
            return 1
        print(f"Public endpoint: OK ({len(candles)} candles)")

        if not (key and secret):
            print("Private endpoint: пропущен (ключи не заданы)")
            return 0
        try:
            bals = await c.get_account_balance()
        except Exception as exc:
            print(f"Private endpoint failed: {type(exc).__name__}")
            return 1

        print(f"Private endpoint: OK ({len(bals)} balances)")
        return 0
    finally:
        await c.close()


raise SystemExit(asyncio.run(main()))
