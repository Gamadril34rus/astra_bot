#!/usr/bin/env python3
"""Paper-clock: только Зевс (wedge false-break retest 4h) на BTC.

НЕ трогает production settings.yaml / live.
Стратегия в коде по умолчанию enabled=False; здесь включаем явно для paper.

После каждого step:
  - structure_state / reject (почему нет входа)
  - ltf_impulse на 15m (память сильного хода, не ордер)
  - tick

Журнал: models/zeus_trade_journal.jsonl
Сопровождение: structural_stop + SMART BE/trail в TradingEngine.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from statistics import median

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from astra_bot.adapters.bingx import BingXClient
from astra_bot.core.logger import setup_logging
from astra_bot.decision.config import DecisionConfig
from astra_bot.decision.pipeline import DecisionPipeline
from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig
from astra_bot.decision.zeus_trade_log import ZeusTradeLog
from astra_bot.strategies.zeus_wedge_retest import (
    ZeusWedgeRetestConfig,
    ZeusWedgeRetestStrategy,
)

logger = logging.getLogger("paper_zeus")

# 15m impulse: range vs median of last N bars, volume vs median
LTF_IMPULSE_RANGE_MULT = 1.8
LTF_IMPULSE_VOL_MULT = 1.6
LTF_LOOKBACK = 24


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Zeus paper-clock (BTC 4h, no live)")
    p.add_argument("--symbol", default="BTC-USDT", help="BingX swap symbol")
    p.add_argument("--interval", type=int, default=300, help="poll seconds")
    p.add_argument("--capital", type=float, default=2000.0)
    p.add_argument(
        "--once",
        action="store_true",
        help="Один цикл decide/step и выход",
    )
    p.add_argument(
        "--journal",
        default="models/zeus_trade_journal.jsonl",
        help="Путь журнала Зевса",
    )
    return p.parse_args()


async def observe_zeus(
    *,
    bingx: BingXClient,
    zeus: ZeusWedgeRetestStrategy,
    journal: ZeusTradeLog,
    symbol: str,
) -> None:
    """Память: структура 4h, reject, 15m impulse. Не открывает ордера."""
    try:
        candles_4h = await bingx.get_recent_candles(symbol, "4h", limit=80)
        # закрытые бары для diagnose (как pipeline)
        closed_4h = candles_4h[:-1] if len(candles_4h) > 1 else list(candles_4h)
        diag = zeus.diagnose(closed_4h)
        journal.structure_state(symbol=symbol, snapshot=diag)

        if diag.get("would_signal"):
            logger.info(
                "Zeus WOULD signal %s %s (paper step decides execution)",
                diag.get("direction"),
                diag.get("pattern"),
            )
        else:
            reason = str(diag.get("reject_reason") or "no_setup")
            journal.reject(
                symbol=symbol,
                reason=reason,
                stage=str(diag.get("stage") or "pattern"),
                snapshot=diag,
            )
            logger.info("Zeus reject: %s stage=%s", reason, diag.get("stage"))

        # 15m impulse — только наблюдение
        candles_15 = await bingx.get_recent_candles(symbol, "15m", limit=LTF_LOOKBACK + 5)
        if len(candles_15) >= 8:
            closed_15 = candles_15[:-1] if len(candles_15) > 1 else candles_15
            last = closed_15[-1]
            lo = float(last.low)
            hi = float(last.high)
            cl = float(last.close)
            op = float(last.open)
            mid = (hi + lo) / 2.0 if hi + lo else cl
            range_pct = ((hi - lo) / mid) if mid else 0.0
            ranges = []
            vols = []
            for b in closed_15[-LTF_LOOKBACK:]:
                m = (float(b.high) + float(b.low)) / 2.0
                if m > 0:
                    ranges.append((float(b.high) - float(b.low)) / m)
                vols.append(float(b.volume or 0))
            med_r = median(ranges) if ranges else 0.0
            med_v = median(vols) if vols else 0.0
            vol = float(last.volume or 0)
            vol_ratio = (vol / med_v) if med_v > 0 else 0.0
            strong = (
                med_r > 0
                and range_pct >= med_r * LTF_IMPULSE_RANGE_MULT
                and (med_v <= 0 or vol_ratio >= LTF_IMPULSE_VOL_MULT)
            )
            if strong:
                direction = "up" if cl >= op else "down"
                near = False
                note = "15m impulse observed; not an entry"
                if diag.get("has_wedge"):
                    upper = float(diag.get("wedge_upper") or 0)
                    lower = float(diag.get("wedge_lower") or 0)
                    if upper and lower:
                        # близко к границе клина (±0.4%)
                        band = mid * 0.004
                        near = abs(cl - upper) <= band or abs(cl - lower) <= band
                        if near:
                            note = "15m impulse near 4h wedge boundary; still not entry without full pattern"
                journal.ltf_impulse(
                    symbol=symbol,
                    timeframe="15m",
                    range_pct=range_pct,
                    volume_ratio=vol_ratio,
                    direction=direction,
                    near_structure=near,
                    note=note,
                )
                logger.info(
                    "LTF impulse 15m %s range_pct=%.4f vol_x=%.2f near_wedge=%s",
                    direction,
                    range_pct,
                    vol_ratio,
                    near,
                )
    except Exception as exc:
        logger.warning("observe_zeus skipped: %s", exc)


async def amain(args: argparse.Namespace) -> int:
    setup_logging()

    api_key = os.environ.get("BINGX_API_KEY", "")
    api_secret = os.environ.get("BINGX_API_SECRET", "")
    bingx = BingXClient(
        {
            "api_key": api_key,
            "api_secret": api_secret,
            "enabled": True,
            "rate_limit_qps": 5,
        }
    )
    await bingx.initialize()

    if not api_key:
        logger.info("BINGX_API_KEY не задан — публичные данные BingX")

    config = TradingEngineConfig(
        symbols=(args.symbol,),
        poll_interval_seconds=args.interval,
        max_open_positions=2,
        structural_stop=True,
        smart_exit_default=True,
    )

    zeus_cfg = ZeusWedgeRetestConfig(enabled=True)
    zeus = ZeusWedgeRetestStrategy(zeus_cfg)
    dcfg = DecisionConfig()
    dcfg.min_rr = 1.5
    dcfg.min_ml_probability = 0.0
    dcfg.min_expected_edge_pct = 0.0
    dcfg.min_ev_r = 0.0
    pipeline = DecisionPipeline(config=dcfg, strategies=[zeus], model=None)

    engine = TradingEngine(
        exchange=bingx,
        pipeline=pipeline,
        config=config,
        notifier=None,
    )

    journal = ZeusTradeLog(args.journal)
    logger.info(
        "Zeus paper-clock: symbol=%s tf=4h enabled=True journal=%s "
        "(memory=reject+structure+ltf; prod НЕ изменены)",
        args.symbol,
        args.journal,
    )
    journal._write(
        {
            "event": "clock_start",
            "symbol": args.symbol,
            "strategy": "zeus_wedge_retest_4h",
            "note": "paper-only; memory layer on; live settings untouched",
        }
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    async def one_cycle() -> None:
        await engine.step()
        await observe_zeus(
            bingx=bingx, zeus=zeus, journal=journal, symbol=args.symbol
        )
        journal._write(
            {
                "event": "tick",
                "symbol": args.symbol,
                "strategy": "zeus_wedge_retest_4h",
                "note": "engine.step + observe completed",
            }
        )

    if args.once:
        await one_cycle()
        logger.info("Один цикл Zeus paper-clock завершён.")
    else:
        # локальный forever: step + observe каждые interval
        while not stop.is_set():
            try:
                await one_cycle()
            except Exception as exc:
                logger.exception("cycle error: %s", exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=args.interval)
            except TimeoutError:
                pass

    await bingx.close()
    return 0


def main() -> None:
    args = parse_args()
    try:
        code = asyncio.run(amain(args))
    except KeyboardInterrupt:
        code = 0
    sys.exit(code or 0)


if __name__ == "__main__":
    main()
