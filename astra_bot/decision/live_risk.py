"""Live risk limits from trades.jsonl (paper parity README: day 4% / week 6%).

Kill-switch: n>=5 closed exits with pnl, profit_factor < 1 → HALT.
Does not change paper thresholds — live-only accounting.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TRADES_PATH = Path("models/zeus_live_trades.jsonl")
KILL_STATE_PATH = Path("models/kill_switch_state.json")

DAY_LOSS_LIMIT = Decimal("0.04")  # README 4%
WEEK_LOSS_LIMIT = Decimal("0.06")  # README 6%
PF_MIN_N = 5
PF_TRIGGER = Decimal("1")  # halt if PF < 1


def _impute_null_pnl(row: dict[str, Any], equity: Decimal | None = None) -> float:
    """B-002: exit with pnl=null → −1R (R from entry row) or −1% equity."""
    # Prefer explicit risk_budget / R on the exit row
    for key in ("risk_budget", "R", "r_dollar"):
        if row.get(key) is not None:
            try:
                r = abs(float(row[key]))
                if r > 0:
                    return -r
            except (TypeError, ValueError):
                pass
    if equity is not None and equity > 0:
        return float(-equity * Decimal("0.01"))
    return -1.0


def _read_exit_rows(path: Path = TRADES_PATH, *, require_pnl: bool = True) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            if row.get("event") != "exit":
                continue
            if require_pnl:
                if row.get("pnl") is None:
                    continue
                try:
                    float(row["pnl"])
                except (TypeError, ValueError):
                    continue
            rows.append(row)
    except OSError as exp:
        logger.warning("live_risk read trades: %s", type(exp).__name__)
    return rows


def _read_exit_pnls(path: Path = TRADES_PATH) -> list[dict[str, Any]]:
    """PF path: only exits with pnl≠null."""
    return _read_exit_rows(path, require_pnl=True)


def sum_pnl_since(
    since_ts: float,
    path: Path = TRADES_PATH,
    *,
    equity: Decimal | None = None,
) -> Decimal:
    """Day/week: include pnl=null as −1R (B-002). PF still uses _read_exit_pnls only."""
    total = Decimal("0")
    for row in _read_exit_rows(path, require_pnl=False):
        try:
            ts = float(row.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ts < since_ts:
            continue
        if row.get("pnl") is None:
            total += Decimal(str(_impute_null_pnl(row, equity)))
        else:
            try:
                total += Decimal(str(row["pnl"]))
            except Exception:
                total += Decimal(str(_impute_null_pnl(row, equity)))
    return total


def utc_day_start_ts(now: datetime | None = None) -> float:
    n = (now or datetime.now(tz=UTC)).astimezone(UTC)
    start = datetime(n.year, n.month, n.day, tzinfo=UTC)
    return start.timestamp()


def utc_week_start_ts(now: datetime | None = None) -> float:
    """Monday 00:00 UTC."""
    n = (now or datetime.now(tz=UTC)).astimezone(UTC)
    monday = n - timedelta(days=n.weekday())
    start = datetime(monday.year, monday.month, monday.day, tzinfo=UTC)
    return start.timestamp()


def profit_factor(path: Path = TRADES_PATH) -> tuple[Decimal | None, int]:
    """PF = gross_win / abs(gross_loss); only exits with pnl. Returns (pf, n)."""
    wins = Decimal("0")
    losses = Decimal("0")
    n = 0
    for row in _read_exit_pnls(path):
        p = Decimal(str(row["pnl"]))
        n += 1
        if p > 0:
            wins += p
        elif p < 0:
            losses += abs(p)
    if n < PF_MIN_N:
        return None, n
    if losses == 0:
        return Decimal("999") if wins > 0 else None, n
    return wins / losses, n


def check_day_week_limits(
    equity: Decimal,
    path: Path = TRADES_PATH,
    now: datetime | None = None,
) -> str | None:
    """Return halt reason or None. Loss limit as fraction of equity."""
    if equity <= 0:
        return None
    day_pnl = sum_pnl_since(utc_day_start_ts(now), path, equity=equity)
    if day_pnl < 0 and abs(day_pnl) / equity >= DAY_LOSS_LIMIT:
        return f"day_loss {float(day_pnl):.2f} >= {float(DAY_LOSS_LIMIT)*100:.0f}% equity"
    week_pnl = sum_pnl_since(utc_week_start_ts(now), path, equity=equity)
    if week_pnl < 0 and abs(week_pnl) / equity >= WEEK_LOSS_LIMIT:
        return f"week_loss {float(week_pnl):.2f} >= {float(WEEK_LOSS_LIMIT)*100:.0f}% equity"
    return None


def check_pf_kill(path: Path = TRADES_PATH, state_path: Path = KILL_STATE_PATH) -> str | None:
    """If n>=5 and PF<1 → persist halt and return reason."""
    pf, n = profit_factor(path)
    if pf is None:
        return None
    if pf >= PF_TRIGGER:
        return None
    reason = f"PF={float(pf):.3f} n={n} < 1"
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "halted": True,
            "reason": reason,
            "pf": float(pf),
            "n": n,
            "ts": time.time(),
        }
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        tmp.replace(state_path)
    except OSError as exc:
        logger.warning("kill state save: %s", type(exc).__name__)
    return reason


def load_kill_halted(state_path: Path = KILL_STATE_PATH) -> str | None:
    if not state_path.exists():
        return None
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        if raw.get("halted"):
            return str(raw.get("reason") or "kill_switch")
    except Exception:
        # B-004: unreadable kill file → treat as halted
        return "kill_state_unreadable"
    return None
