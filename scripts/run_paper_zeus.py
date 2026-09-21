#!/usr/bin/env python3
"""Zeus paper-clock: wedge + channel (lessons), multi-symbol, paper-only."""

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
from astra_bot.strategies.zeus_channel_boundary import (
    ZeusChannelBoundaryConfig,
    ZeusChannelBoundaryStrategy,
)
from astra_bot.strategies.zeus_wedge_retest import (
    ZeusWedgeRetestConfig,
    ZeusWedgeRetestStrategy,
)

logger = logging.getLogger("paper_zeus")

LTF_IMPULSE_RANGE_MULT = 1.8
LTF_IMPULSE_VOL_MULT = 1.6
LTF_LOOKBACK = 24

ZEUS_STATE_PATH = "models/zeus_paper_positions.json"
ZEUS_TRADES_PATH = "models/zeus_paper_trades.jsonl"
ZEUS_STATS_PATH = "models/zeus_strategy_stats.json"
ZEUS_NO_TRADE_OBS = "models/zeus_no_trade_observations.jsonl"
ZEUS_NO_TRADE_OUT = "models/zeus_no_trade_outcomes.json"
ZEUS_PATTERN_EXIT = "models/zeus_pattern_exit_shadow.jsonl"
ZEUS_HALT_ALERTS = "models/zeus_halt_alerts.json"
ZEUS_HYPOTHESES = "models/zeus_hypotheses.json"
ZEUS_KLINES_CACHE = "models/zeus_klines_cache"

DEFAULT_SYMBOLS = (
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "BNB-USDT",
    "XRP-USDT",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Zeus paper multi-symbol")
    p.add_argument("--symbol", default="")
    p.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    p.add_argument("--interval", type=int, default=300)
    p.add_argument("--capital", type=float, default=2000.0)
    p.add_argument("--once", action="store_true")
    p.add_argument("--journal", default="models/zeus_trade_journal.jsonl")
    return p.parse_args()


def resolve_symbols(args: argparse.Namespace) -> tuple[str, ...]:
    if (args.symbol or "").strip():
        return (args.symbol.strip().upper(),)
    parts = [s.strip().upper() for s in (args.symbols or "").split(",") if s.strip()]
    return tuple(parts) if parts else DEFAULT_SYMBOLS


def _snap_positions(engine: TradingEngine) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    try:
        for p in engine.broker.positions or []:
            tps = getattr(p, "take_profits", None) or []
            tp0 = ""
            if tps:
                first = tps[0]
                tp0 = (
                    str(first)
                    if not isinstance(first, dict)
                    else str(first.get("price", ""))
                )
            out[str(p.id)] = {
                "stop_loss": str(p.stop_loss),
                "entry_price": str(p.entry_price),
                "direction": str(getattr(p, "direction", "") or ""),
                "quantity": str(p.quantity),
                "symbol": getattr(p, "symbol", "") or "",
                "strategy": getattr(p, "strategy", "") or "",
                "take_profit": tp0,
            }
    except Exception as exc:
        logger.debug("snap: %s", exc)
    return out


def _load_known_trade_ids(trades_path: Path) -> set[str]:
    known: set[str] = set()
    if not trades_path.exists():
        return known
    try:
        import json as _json

        for line in trades_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = _json.loads(line)
            tid = str(row.get("id") or row.get("trade_id") or "")
            if tid:
                known.add(tid)
    except Exception as exc:
        logger.debug("known ids: %s", exc)
    return known


def sync_journal_from_broker(
    *,
    engine: TradingEngine,
    journal: ZeusTradeLog,
    before: dict[str, dict[str, Any]],
    trades_path: Path,
    known_trade_ids: set[str],
) -> set[str]:
    after = _snap_positions(engine)
    for pid, meta in after.items():
        if pid not in before:
            try:
                journal.entry(
                    symbol=str(meta.get("symbol") or "BTC-USDT"),
                    direction=str(meta.get("direction") or ""),
                    entry_price=meta.get("entry_price") or "0",
                    stop_loss=meta.get("stop_loss") or "0",
                    take_profit=str(meta.get("take_profit") or "0"),
                    reason=f"paper_entry position_id={pid}",
                    features={
                        "position_id": pid,
                        "quantity": str(meta.get("quantity") or ""),
                        "source": "zeus_paper_clock",
                    },
                    strategy=str(
                        meta.get("strategy") or "zeus_channel_boundary_4h"
                    ),
                )
            except Exception as exc:
                logger.warning("journal.entry failed: %s", exc)
        elif before[pid].get("stop_loss") != meta.get("stop_loss"):
            try:
                journal.stop_adjust(
                    symbol=str(meta.get("symbol") or "BTC-USDT"),
                    direction=str(
                        meta.get("direction")
                        or before[pid].get("direction")
                        or ""
                    ),
                    old_stop=before[pid].get("stop_loss") or "0",
                    new_stop=meta.get("stop_loss") or "0",
                    why=f"stop_sync position_id={pid}",
                )
            except Exception as exc:
                logger.warning("journal.stop_adjust failed: %s", exc)
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
                try:
                    journal.exit(
                        symbol=str(row.get("symbol") or "BTC-USDT"),
                        direction=str(row.get("direction") or ""),
                        exit_price=row.get("exit_price")
                        or row.get("close_price")
                        or "0",
                        reason=str(
                            row.get("exit_reason")
                            or row.get("reason")
                            or "closed"
                        ),
                    )
                except Exception as exc:
                    logger.warning("journal.exit failed: %s", exp)
        except Exception as exc:
            logger.warning("exit sync: %s", exp)
    return known_trade_ids


async def observe_zeus(
    *,
    bingx: BingXClient,
    zeus: ZeusWedgeRetestStrategy,
    journal: ZeusTradeLog,
    symbol: str,
    zeus_channel: ZeusChannelBoundaryStrategy | None = None,
) -> None:
    try:
        klines = await bingx.get_candles(symbol, "4h", limit=80)
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
        if zeus_channel is not None:
            diag_ch = zeus_channel.diagnose(closed_4h)
            snap = dict(diag_ch)
            snap["structure"] = "channel"
            journal.structure_state(symbol=symbol, snapshot=snap)
            if not diag_ch.get("would_signal") and diag_ch.get("reject_reason"):
                journal.reject(
                    symbol=symbol,
                    reason="channel:" + str(diag_ch.get("reject_reason") or ""),
                    stage=str(diag_ch.get("stage") or ""),
                    snapshot=snap,
                )
        try:
            k15 = await bingx.get_candles(symbol, "15m", limit=40)
            if k15 and len(k15) >= LTF_LOOKBACK:
                tail = k15[-LTF_LOOKBACK:]
                ranges = [float(c.high) - float(c.low) for c in tail]
                vols = [float(getattr(c, "volume", 0) or 0) for c in tail]
                avg_r = sum(ranges[:-1]) / max(len(ranges) - 1, 1)
                avg_v = sum(vols[:-1]) / max(len(vols) - 1, 1)
                last_r, last_v = ranges[-1], vols[-1]
                if avg_r > 0 and last_r >= avg_r * LTF_IMPULSE_RANGE_MULT:
                    journal.ltf_impulse(
                        symbol=symbol,
                        timeframe="15m",
                        note="15m impulse; not entry alone",
                        range_mult=round(last_r / avg_r, 3) if avg_r else None,
                        vol_mult=round(last_v / avg_v, 3) if avg_v else None,
                    )
        except Exception as ltf_exc:
            logger.debug("ltf: %s", ltf_exc)
    except Exception as exc:
        logger.warning("observe_zeus skipped: %s", exp)


async def amain(args: argparse.Namespace) -> int:
    setup_logging()
    symbols = resolve_symbols(args)
    logger.info("Zeus symbols: %s", ",".join(symbols))
    env = (os.environ.get("ENVIRONMENT") or "").strip().lower()
    paper_flag = (os.environ.get("PAPER_TRADING") or "").strip().lower()
    if env and env not in ("paper", "test", "dev"):
        logger.error("ABORT: ENVIRONMENT=%s", env)
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

    config = TradingEngineConfig(
        symbols=symbols,
        poll_interval_seconds=args.interval,
        max_open_positions=5,
        structural_stop=True,
        smart_exit_default=True,
        state_path=ZEUS_STATE_PATH,
        trades_path=ZEUS_TRADES_PATH,
        stats_path=ZEUS_STATS_PATH,
        no_trade_observations_path=ZEUS_NO_TRADE_OBS,
        no_trade_outcomes_path=ZEUS_NO_TRADE_OUT,
        pattern_exit_shadow_path=ZEUS_PATTERN_EXIT,
        halt_alerts_path=ZEUS_HALT_ALERTS,
        hypotheses_path=ZEUS_HYPOTHESES,
        klines_cache_dir=ZEUS_KLINES_CACHE,
        entry_gates_enabled=False,
        entry_gates_shadow_enabled=True,
        entry_gate_block_regimes=frozenset(),
    )

    zeus = ZeusWedgeRetestStrategy(ZeusWedgeRetestConfig(enabled=True))
    zeus_channel = ZeusChannelBoundaryStrategy(
        ZeusChannelBoundaryConfig(enabled=True)
    )
    dcfg = DecisionConfig()
    dcfg.min_rr = 1.5
    dcfg.min_ml_probability = 0.0
    dcfg.min_expected_edge_pct = 0.0
    dcfg.min_ev_r = 0.0
    pipeline = DecisionPipeline(
        config=dcfg, strategies=[zeus, zeus_channel], model=None
    )
    engine = TradingEngine(
        exchange=bingx, pipeline=pipeline, config=config, notifier=None
    )

    from decimal import Decimal as _Dec

    _cap = _Dec(str(args.capital))
    if _cap <= 0:
        _cap = _Dec("2000")
    try:
        br = engine.broker
        old_cap = getattr(br, "initial_capital", None)
        br.initial_capital = _cap
        if hasattr(engine, "risk") and hasattr(engine.risk, "set_capital"):
            engine.risk.set_capital(_cap, _cap)
        engine._capital_synced = True
        try:
            br.save()
        except Exception:
            pass
        logger.info("Zeus capital fixed to %s (was %s)", _cap, old_cap)
    except Exception as _cap_exc:
        logger.warning("capital fix skipped: %s", _cap_exc)

    journal = ZeusTradeLog(args.journal)
    trades_path = Path(ZEUS_TRADES_PATH)
    known_ids = _load_known_trade_ids(trades_path)
    journal._write(
        {
            "event": "clock_start",
            "symbol": ",".join(symbols),
            "strategy": "zeus_wedge+channel",
            "note": "paper; wedge+channel lessons; capital="
            + str(args.capital),
        }
    )

    for pid, meta in _snap_positions(engine).items():
        try:
            journal.entry(
                symbol=str(meta.get("symbol") or "BTC-USDT"),
                direction=str(meta.get("direction") or ""),
                entry_price=meta.get("entry_price") or "0",
                stop_loss=meta.get("stop_loss") or "0",
                take_profit=str(meta.get("take_profit") or "0"),
                reason=f"backfill_open position_id={pid}",
                features={
                    "position_id": pid,
                    "quantity": str(meta.get("quantity") or ""),
                    "source": "backfill",
                },
                strategy=str(meta.get("strategy") or ""),
            )
        except Exception as exc:
            logger.warning("backfill entry failed: %s", exp)

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
        for sym in symbols:
            await observe_zeus(
                bingx=bingx,
                zeus=zeus,
                zeus_channel=zeus_channel,
                journal=journal,
                symbol=sym,
            )
        n_open = len(engine.broker.positions or [])
        journal._write(
            {
                "event": "tick",
                "symbol": ",".join(symbols),
                "strategy": "zeus_wedge+channel",
                "note": "cycle multi wedge+channel",
                "open_positions": n_open,
            }
        )

    if args.once:
        await one_cycle()
        logger.info(
            "Zeus cycle done open=%d", len(engine.broker.positions or [])
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
