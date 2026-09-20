#!/usr/bin/env python3
"""Zeus paper-clock: BTC 4h wedge research, isolated state, no live.

Стратегия в коде по умолчанию enabled=False; здесь включаем явно для paper.
Полный цикл сделки:
  - entry / stop_adjust / exit в zeus_trade_journal (из paper-брокера)
  - observe_zeus: structure_state / reject / ltf_impulse

Изоляция state: все path-поля TradingEngineConfig → models/zeus_*
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
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
        for p in engine.broker.positions or []:
            out[str(p.id)] = {
                "stop_loss": str(p.stop_loss),
                "entry_price": str(p.entry_price),
                "direction": p.direction,
                "quantity": str(p.quantity),
                "strategy": getattr(p, "strategy", "") or "zeus_wedge_retest_4h",
            }
    except Exception as exc:
        logger.debug("snap positions: %s", exc)
    return out


def _load_known_trade_ids(trades_path: Path) -> set[str]:
    known: set[str] = set()
    if not trades_path.exists():
        return known
    try:
        for line in trades_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            import json as _json

            row = _json.loads(line)
            tid = str(row.get("id") or row.get("trade_id") or "")
            if tid:
                known.add(tid)
    except Exception as exc:
        logger.debug("known trade ids: %s", exc)
    return known


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
                symbol=getattr(engine, "_last_symbol", None) or "BTC-USDT",
                direction=meta["direction"],
                entry_price=meta["entry_price"],
                stop_loss=meta["stop_loss"],
                quantity=meta.get("quantity"),
                notes={
                    "position_id": pid,
                    "strategy": meta.get("strategy"),
                    "source": "zeus_paper_clock",
                },
                strategy=str(meta.get("strategy") or "zeus_wedge_retest_4h"),
            )
        else:
            old_sl = before[pid].get("stop_loss")
            new_sl = meta.get("stop_loss")
            if old_sl != new_sl:
                journal.stop_adjust(
                    symbol="BTC-USDT",
                    position_id=pid,
                    old_stop=old_sl,
                    new_stop=new_sl,
                    entry_price=meta["entry_price"],
                )
    # Новые закрытия в zeus_paper_trades.jsonl → exit
    if trades_path.exists():
        import json as _json

        try:
            for line in trades_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = _json.loads(line)
                tid = str(row.get("id") or row.get("trade_id") or "")
                if not tid or tid in known_trade_ids:
                    continue
                known_trade_ids.add(tid)
                journal.exit(
                    symbol=str(row.get("symbol") or "BTC-USDT"),
                    direction=str(row.get("direction") or ""),
                    entry_price=row.get("entry_price"),
                    exit_price=row.get("exit_price") or row.get("close_price"),
                    pnl=row.get("pnl") or row.get("realized_pnl"),
                    reason=str(row.get("exit_reason") or row.get("reason") or ""),
                    notes={"trade_id": tid, "source": "zeus_paper_clock"},
                )
        except Exception as exc:
            logger.debug("exit sync: %s", exp)
    return known_trade_ids


async def observe_zeus(
    *,
    bingx: BingXClient,
    zeus: ZeusWedgeRetestStrategy,
    journal: ZeusTradeLog,
    symbol: str,
) -> None:
    try:
        klines = await bingx.get_klines(symbol, interval="4h", limit=80)
        closed_4h = list(klines) if klines else []
        if len(closed_4h) >= 2:
            closed_4h = closed_4h[:-1]
        diag = zeus.diagnose(closed_4h)
        journal.structure_state(symbol=symbol, snapshot=diag)
        if not diag.get("would_signal") and diag.get("reject_reason"):
            journal.reject(
                symbol=symbol,
                reason=str(diag.get("reject_reason") or ""),
                stage=str(diag.get("stage") or ""),
                snapshot=diag,
            )
        # LTF impulse observation (15m) — not entry
        try:
            k15 = await bingx.get_klines(symbol, interval="15m", limit=40)
            if k15 and len(k15) >= LTF_LOOKBACK:
                tail = k15[-LTF_LOOKBACK:]
                ranges = [float(c.high) - float(c.low) for c in tail]
                vols = [float(getattr(c, "volume", 0) or 0) for c in tail]
                avg_r = sum(ranges[:-1]) / max(len(ranges) - 1, 1)
                avg_v = sum(vols[:-1]) / max(len(vols) - 1, 1)
                last_r = ranges[-1]
                last_v = vols[-1]
                if avg_r > 0 and last_r >= avg_r * LTF_IMPULSE_RANGE_MULT:
                    note = "15m impulse observed; not an entry"
                    if avg_v > 0 and last_v >= avg_v * LTF_IMPULSE_VOL_MULT:
                        note = (
                            "15m impulse+volume observed; "
                            "still not entry without full pattern"
                        )
                    journal.ltf_impulse(
                        symbol=symbol,
                        timeframe="15m",
                        note=note,
                        range_mult=round(last_r / avg_r, 3) if avg_r else None,
                        vol_mult=round(last_v / avg_v, 3) if avg_v else None,
                    )
        except Exception as ltf_exc:
            logger.debug("ltf observe: %s", ltf_exc)
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
        # Paper-research: живой блок гейтов ВЫКЛ. Тень пишем, но REGIME
        # не должен даже в shadow создавать шум — block_regimes пустой.
        entry_gates_enabled=False,
        entry_gates_shadow_enabled=True,
        entry_gate_block_regimes=frozenset(),
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

    # Критично: state мог сохраниться с initial_capital=0 → size всегда 0,
    # entry не открывается даже при would_signal. --capital восстанавливает.
    from decimal import Decimal as _Dec

    _cap = _Dec(str(args.capital))
    if _cap <= 0:
        _cap = _Dec("2000")
    try:
        br = engine.broker
        cur = getattr(br, "initial_capital", None)
        if cur is None or _Dec(str(cur)) <= 0:
            br.initial_capital = _cap
            if hasattr(engine, "risk") and hasattr(engine.risk, "set_capital"):
                engine.risk.set_capital(_cap, _cap)
            elif hasattr(engine, "risk") and hasattr(engine.risk, "initial_capital"):
                engine.risk.initial_capital = _cap
            br.save()
            logger.info("Zeus paper capital restored to %s (was %s)", _cap, cur)
        engine._capital_synced = True  # не затирать sync_capital с API в 0
    except Exception as _cap_exc:
        logger.warning("capital restore skipped: %s", _cap_exc)

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
                "entry/stop/exit journal; REGIME not blocking; capital="
                + str(args.capital)
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
                logger.exception("cycle error: %s", exp)
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
