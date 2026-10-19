"""P5: stop-change telemetry for live (owner 19.10).

Write every stop mutation so trail/BE can be validated externally.
State file models/zeus_live_stop_log.jsonl must be in workflow save-list.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

STOP_LOG_PATH = Path("models/zeus_live_stop_log.jsonl")


def record_stop_change(
    *,
    symbol: str,
    stop_price: str | float,
    source: str,
    reason: str = "",
    side: str = "",
    path: Path | None = None,
) -> None:
    """Append one stop-change row. Fail-open."""
    p = path or STOP_LOG_PATH
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        row: dict[str, Any] = {
            "ts": time.time(),
            "symbol": symbol,
            "side": side,
            "stop_price": str(stop_price),
            "source": source,
            "reason": reason,
        }
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass
