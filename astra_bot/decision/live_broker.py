"""LiveBroker: exchange is truth; hard cap $100; risk % of real equity.

Does not touch PaperBroker. HALT/kill checked before any place_order.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


def append_live_trade(row: dict[str, Any], path: Path | None = None) -> None:
    """Append one exit/entry row to models/zeus_live_trades.jsonl."""
    p = path or Path("models/zeus_live_trades.jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    with p.open("a", encoding="utf-8") as f:
        f.write(_json.dumps(row, ensure_ascii=False, default=str) + "\n")


HARD_CAP_USD = Decimal("100")

_ORDER_SEQ = 0


def unique_client_order_id(symbol: str, role: str) -> str:
    """BingX forbids repeating clientOrderId (30248). Unique per order.

    Format: arm-<ms>-<seq>-<first4 base>-<role>  role in e|s|t|r …
    """
    global _ORDER_SEQ
    _ORDER_SEQ += 1
    base = (symbol or "X").split("-")[0][:4]
    ms = time.time_ns() // 1_000_000
    return f"arm-{ms}-{_ORDER_SEQ}-{base}-{role}"
