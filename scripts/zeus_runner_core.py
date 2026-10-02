"""Zeus paper-clock split 2/9: args, symbols, position snapshot, known ids."""

from __future__ import annotations

import argparse

from zeus_runner_common import (
    Any,
    DEFAULT_SYMBOLS,
    Path,
    TradingEngine,
    logger,
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
