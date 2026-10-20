"""P6/P7 funnel attribution (owner 19.10)."""
from __future__ import annotations
from typing import Any

SKIP_KEYS = (
    "same_side_guard", "vol_cooldown", "conf", "margin",
    "margin_M2", "margin_M3", "margin_M4", "equity_unknown",
    "sized_qty_zero",
    "max_open", "cooldown", "symbol_conflict", "retest_not_triggered",
    "htf_gate", "filters", "entries_per_tick_limit", "other",
)

def empty_skipped() -> dict[str, int]:
    return {k: 0 for k in SKIP_KEYS}

def bump(skipped: dict[str, int], reason: str) -> None:
    if reason not in skipped:
        skipped["other"] = int(skipped.get("other") or 0) + 1
        return
    skipped[reason] = int(skipped.get(reason) or 0) + 1

def identity_ok(candidates: int, entered: list, skipped: dict[str, int]) -> bool:
    return int(candidates) == len(entered) + sum(int(v or 0) for v in skipped.values())

def unattributed(skipped: dict[str, int]) -> int:
    return int(skipped.get("other") or 0)

def arm_summary_row(*, candidates: int, entered: list, skipped: dict[str, int],
                    unrealized_sum: float | None, dry: bool, entries_allowed: bool,
                    halt: str | None, ts: float) -> dict[str, Any]:
    return {
        "event": "arm_summary", "dry": dry, "entries_allowed": entries_allowed,
        "halt": halt, "candidates": candidates, "entered": entered, "skipped": skipped,
        "unrealized_sum": unrealized_sum,
        "identity_ok": identity_ok(candidates, entered, skipped),
        "unattributed": unattributed(skipped),
        "ts": ts,
    }
