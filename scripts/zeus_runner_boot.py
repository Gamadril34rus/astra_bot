"""Zeus paper-clock split 7/9: journal init, clock_start, open-position backfill."""

from __future__ import annotations

import argparse

from zeus_runner_common import (
    Path,
    TradingEngine,
    ZeusTradeLog,
    ZEUS_TRADES_PATH,
    logger,
)
from zeus_runner_core import _load_known_trade_ids, _snap_positions

def zeus_journal_boot(
    engine: TradingEngine, args: argparse.Namespace, symbols: tuple[str, ...]
) -> tuple[ZeusTradeLog, Path, set[str]]:
    journal = ZeusTradeLog(args.journal)
    trades_path = Path(ZEUS_TRADES_PATH)
    known_ids = _load_known_trade_ids(trades_path)
    journal._write(
        {
            "event": "clock_start",
            "symbol": ",".join(symbols),
            "strategy": "zeus_channel+wedge",
            "note": "paper; channel TP2 + matched wedge; lev_conf max20; capital="
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
            logger.warning("backfill entry failed: %s", exc)
    return journal, trades_path, known_ids
