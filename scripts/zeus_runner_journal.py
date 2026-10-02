"""Zeus paper-clock split 3/9: broker->journal sync + trail delegate."""

from __future__ import annotations

from zeus_runner_common import (
    Any,
    BingXClient,
    Path,
    TradingEngine,
    ZeusTradeLog,
    logger,
)
from zeus_runner_core import _snap_positions

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
                    logger.warning("journal.exit failed: %s", exc)
        except Exception as exc:
            logger.warning("exit sync: %s", exc)
    return known_trade_ids

async def zeus_trail_open_positions(
    *,
    engine: TradingEngine,
    journal: ZeusTradeLog,
    bingx: BingXClient,
) -> None:
    """Delegate to zeus_trail_helpers (sanitize + late trail)."""
    import sys as _sys
    from pathlib import Path as _P

    _scripts = str(_P(__file__).resolve().parent)
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    from zeus_trail_helpers import zeus_trail_open_positions as _trail

    await _trail(engine=engine, journal=journal, bingx=bingx)
