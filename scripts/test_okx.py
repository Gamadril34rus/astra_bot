"""Проверка соединения с OKX и валидности API-ключей."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv()
from astra_bot.adapters.okx import OKXClient


async def main():
    cfg = {
        "api_key": os.environ.get("OKX_API_KEY", ""),
        "api_secret": os.environ.get("OKX_API_SECRET", ""),
        "passphrase": os.environ.get("OKX_API_PASSPHRASE") or os.environ.get("OKX_PASSPHRASE", ""),
    }
    print(f"Using key: {cfg['api_key'][:4]}... passphrase=***")
    c = OKXClient(cfg)
    await c.initialize()
    try:
        # Public endpoint — проверка сети
        candles = await c.get_candles("BTC-USDT", timeframe="1D", limit=5)
        print(f"Public candles: {len(candles)}")
        for x in candles[-3:]:
            print(f"  open_time={x.open_time} O={x.open} H={x.high} L={x.low} C={x.close}")
        # Private endpoint — проверка ключей
        try:
            bals = await c.get_account_balance()
            print(f"Account balances: {len(bals)}")
            for asset, b in list(bals.items())[:5]:
                print(f"  {asset}: free={b.free} locked={b.locked} total={b.total}")
        except Exception as e:
            print(f"PRIVATE endpoint failed: {type(e).__name__}: {e}")
    finally:
        await c.close()

asyncio.run(main())
