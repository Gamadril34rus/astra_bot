#!/usr/bin/env python3
"""Paper-clock: только Зевс (wedge false-break retest 4h) на BTC.

НЕ трогает production settings.yaml / live.
Стратегия в коде по умолчанию enabled=False; здесь включаем явно для paper.

После каждого step:
  - entry / stop_adjust / exit в zeus_trade_journal (из paper-брокера)
  - structure_state / reject / ltf_impulse (память)
  - tick

Изоляция state: все path-поля TradingEngineConfig → models/zeus_*
(не пересекаются с основным paper_positions / paper_trades).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path
from statistics import median
from typing import Any

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

LTF_IMPULSE_RANGE_MULT = 1.8
LTF_IMPULSE_VOL_MULT = 1.6
LTF_LOOKBACK = 24

# Изолированные пути — ноль пересечений с основным ботом
ZEUS_STATE_PATH = "models/zeus_paper_positions.json"
ZEUS_TRADES_PATH = "models/zeus_paper_trades.jsonl"
ZEUS_STATS_PATH = "models/zeus_strategy_stats.json"
ZEUS_NO_TRADE_OBS = "models/zeus_no_trade_observations.jsonl"
ZEUS_NO_TRADE_OUT = "models/zeus_no_trade_outcomes.json"
ZEUS_PATTERN_EXIT = "models/zeus_pattern_exit_shadow.jsonl"
ZEUS_HALT_ALERTS = "models/zeus_halt_alerts.json"
ZEUS_HYPOTHESES = "models/zeus_hypotheses.json"
ZEUS_KLINES_CACHE = "models/zeus_klines_cache"


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


def _snap_positions(engine: TradingEngine) -> dict[str, dict[str, Any]]:
    """Снимок открытых позиций: id → stop/entry/direction/strategy."""
    out: dict[str, dict[str, Any]] = {}
    try:
        for p in list(engine.broker.positions or []):
            out[str(p.id)] = {
                "symbol": p.symbol,
                "direction": p.direction,
                "entry_price": str(p.entry_price),
                "stop_loss": str(p.stop_loss),
                "take_profit": (
                    str(p.plan_take)
                    if getattr(p, "plan_take", None) is not None
                    else (
                        str(p.take_profits[0])
                        if getattr(p, "take_profits", None)
                        else ""
                    )
                ),
                "strategy": getattr(p, "strategy", "") or "zeus_wedge_retest_4h",
                "trailing": bool(getattr(p, "trailing_activated", False)),
                "bars_held": int(getattr(p, "bars_held", 0) or 0),
            }
    except Exception as exc:
        logger.debug("snap positions: %s", exc)
    return out


def _guess_stop_why(old_stop: float, new_stop: float, direction: str, trailing: bool) -> str:
    if trailing:
        return "trailing"
    try:
        # стоп ближе к входу / за BE
        if direction == "long" and new_stop > old_stop:
            return "breakeven_or_structure"
        if direction == "short" and new_stop < old_stop:
            return "breakeven_or_structure"
    except Exception:
        pass
    return "structure"


def sync_journal_from_broker(
    *,
    engine: TradingEngine,
    journal: ZeusTradeLog,
    before: dict[str, dict[str, Any]],
    trades_path: Path,
    known_trade_ids: set[str],
) -> set[str]:
    """Пишет entry / stop_adjust / exit по диффу брокера. Не ломает reject/*."""
    after = _snap_positions(engine)

    # Новые позиции → entry
    for pid, meta in after.items():
        if pid not in before:
            journal.entry(
                symbol=str(meta["symbol"]),
                direction=str(meta["direction"]),
                entry_price=meta["entry_price"],
                stop_loss=meta["stop_loss"],
                take_profit=meta.get("take_profit") or "",
                reason="paper_open",
                features={
                    "position_id": pid,
                    "strategy": meta.get("strategy"),
                    "source": "zeus_paper_clock",
                },
                strategy=str(meta.get("strategy") or "zeus_wedge_retest_4h"),
            )
            logger.info(
                "Zeus ENTRY %s %s @ %s SL %s",
                meta["direction"],
                meta["symbol"],
                meta["entry_price"],
                meta["stop_loss"],
            )

    # Изменение стопа → stop_adjust
    for pid, meta in after.items():
        if pid not in before:
            continue
        old = before[pid]
        if str(old.get("stop_loss")) != str(meta.get("stop_loss")):
            why = _guess_stop_why(
                float(old["stop_loss"]),
                float(meta["stop_loss"]),
                str(meta["direction"]),
                bool(meta.get("trailing")),
            )
            journal.stop_adjust(
                symbol=str(meta["symbol"]),
                direction=str(meta["direction"]),
                old_stop=old["stop_loss"],
                new_stop=meta["stop_loss"],
                why=why,
                mfe_r=None,
            )
            logger.info(
                "Zeus STOP %s %s → %s (%s)",
                meta["symbol"],
                old["stop_loss"],
                meta["stop_loss"],
                why,
            )

    # Новые закрытия в zeus_paper_trades.jsonl → exit
    if trades_path.exists():
        try:
            for line in trades_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                tid = str(row.get("id") or "")
                if not tid or tid in known_trade_ids:
                    continue
                known_trade_ids.add(tid)
                journal.exit(
                    symbol=str(row.get("symbol") or ""),
                    direction=str(row.get("direction") or ""),
                    exit_price=row.get("exit_price", ""),
                    reason=str(row.get("exit_reason") or "close"),
                    r_multiple=(
                        float(row["r_multiple"])
                        if row.get("r_multiple") is not None
                        else None
                    ),
                    bars_held=None,
                )
                logger.info(
                    "Zeus EXIT %s %s @ %s R=%s reason=%s",
                    row.get("direction"),
                    row.get("symbol"),
                    row.get("exit_price"),
                    row.get("r_multiple"),
                    row.get("exit_reason"),
                )
        except Exception as exc:
            logger.warning("sync exits: %s", exc)

    return known_trade_ids


def _load_known_trade_ids(trades_path: Path) -> set[str]:
    ids: set[str] = set()
    if not trades_path.exists():
        return ids
    try:
        for line in trades_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if row.get("id"):
                    ids.add(str(row["id"]))
            except Exception:
                continue
    except Exception:
        pass
    return ids


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

        candles_15 = await bingx.get_recent_candles(
            symbol, "15m", limit=LTF_LOOKBACK + 5
        )
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
                        band = mid * 0.004
                        near = abs(cl - upper) <= band or abs(cl - lower) <= band
                        if near:
                            note = (
                                "15m impulse near 4h wedge boundary; "
                                "still not entry without full pattern"
                            )
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

    env = (os.environ.get("ENVIRONMENT") or "").strip().lower()
    paper_flag = (os.environ.get("PAPER_TRADING") or "").strip().lower()
    if env and env not in ("paper", "test", "dev"):
        logger.error(
            "ABORT: ENVIRONMENT=%s — Zeus paper-clock только paper", env
        )
        return 2
    if not env:
        os.environ["ENVIRONMENT"] = "paper"
    if paper_flag not in ("1", "true", "yes"):
        os.environ["PAPER_TRADING"] = "true"

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
        # --- полная изоляция state от основного бота ---
        state_path=ZEUS_STATE_PATH,
        trades_path=ZEUS_TRADES_PATH,
        stats_path=ZEUS_STATS_PATH,
        no_trade_observations_path=ZEUS_NO_TRADE_OBS,
        no_trade_outcomes_path=ZEUS_NO_TRADE_OUT,
        pattern_exit_shadow_path=ZEUS_PATTERN_EXIT,
        halt_alerts_path=ZEUS_HALT_ALERTS,
        hypotheses_path=ZEUS_HYPOTHESES,
        klines_cache_dir=ZEUS_KLINES_CACHE,
        # тень entry_gates ок; живой блок выкл (как default)
        entry_gates_enabled=False,
        entry_gates_shadow_enabled=True,
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
    trades_path = Path(ZEUS_TRADES_PATH)
    known_ids = _load_known_trade_ids(trades_path)

    logger.info(
        "Zeus paper-clock: symbol=%s tf=4h state=%s trades=%s "
        "ENVIRONMENT=%s PAPER_TRADING=%s (live paths NOT used)",
        args.symbol,
        ZEUS_STATE_PATH,
        ZEUS_TRADES_PATH,
        os.environ.get("ENVIRONMENT"),
        os.environ.get("PAPER_TRADING"),
    )
    journal._write(
        {
            "event": "clock_start",
            "symbol": args.symbol,
            "strategy": "zeus_wedge_retest_4h",
            "note": (
                "paper-only; isolated zeus_* paths; "
                "entry/stop/exit journal wired; live settings untouched"
            ),
            "state_path": ZEUS_STATE_PATH,
            "trades_path": ZEUS_TRADES_PATH,
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
        nonlocal known_ids
        before = _snap_positions(engine)
        await engine.step()
        known_ids = sync_journal_from_broker(
            engine=engine,
            journal=journal,
            before=before,
            trades_path=trades_path,
            known_trade_ids=known_ids,
        )
        await observe_zeus(
            bingx=bingx, zeus=zeus, journal=journal, symbol=args.symbol
        )
        n_open = len(engine.broker.positions or [])
        journal._write(
            {
                "event": "tick",
                "symbol": args.symbol,
                "strategy": "zeus_wedge_retest_4h",
                "note": "engine.step + trade_sync + observe completed",
                "open_positions": n_open,
            }
        )

    if args.once:
        await one_cycle()
        logger.info(
            "Один цикл Zeus paper-clock завершён. open=%d",
            len(engine.broker.positions or []),
        )
    else:
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
