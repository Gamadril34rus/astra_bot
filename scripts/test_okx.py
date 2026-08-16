"""Проверка соединения с OKX и валидности API-ключей без вывода секретов."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv()
from astra_bot.adapters.okx import OKXClient


async def main() -> int:
    names = ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE")
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        print(f"Missing required OKX variables: {', '.join(missing)}")
        return 1

    cfg = {
        "api_key": os.environ["OKX_API_KEY"],
        "api_secret": os.environ["OKX_API_SECRET"],
        "passphrase": os.environ["OKX_API_PASSPHRASE"],
        "sandbox": os.environ.get("OKX_DEMO", "1").lower() not in {"0", "false", "no"},
    }

    c = OKXClient(cfg)
    await c.initialize()
    try:
        candles = await c.get_candles("BTC-USDT", timeframe="1D", limit=5)
        if not candles:
            print("Public OKX endpoint failed")
            return 1
        print(f"Public endpoint: OK ({len(candles)} candles)")

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
