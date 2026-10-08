"""Zeus weekly confidence-tier audit (money R = pnl / risk_budget).

Does NOT auto-change the ladder — only writes verdict + warning flag.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TRADES_PATH = Path("models/zeus_paper_trades.jsonl")
DEFAULT_AUDIT_PATH = Path("models/zeus_tier_audit.json")
MIN_TOP_N = 30
AUDIT_INTERVAL_DAYS = 7

# Tier labels ordered high→low; bins are [lo, hi)
TIER_BINS: tuple[tuple[str, float, float | None], ...] = (
    ("top_ge_0.88", 0.88, None),  # conf >= 0.88
    ("mid_0.80_0.88", 0.80, 0.88),
    ("low_0.70_0.80", 0.70, 0.80),
    ("base_lt_0.70", 0.0, 0.70),
)


def tier_label(conf: float) -> str:
    for name, lo, hi in TIER_BINS:
        if hi is None:
            if conf >= lo:
                return name
        elif lo <= conf < hi:
            return name
    return "base_lt_0.70"


def money_r(pnl: float, risk_budget: float) -> float | None:
    """R in money terms: pnl / risk_budget. Ignore journal r_multiple."""
    if risk_budget is None or risk_budget <= 0:
        return None
    return float(pnl) / float(risk_budget)


@dataclass
class TierStats:
    n: int = 0
    sum_r: float = 0.0

    @property
    def exp(self) -> float | None:
        if self.n <= 0:
            return None
        return self.sum_r / self.n

    def add(self, r: float) -> None:
        self.n += 1
        self.sum_r += r

    def to_dict(self) -> dict[str, Any]:
        return {"n": self.n, "sum_r": round(self.sum_r, 6), "exp": None if self.exp is None else round(self.exp, 6)}


def aggregate_tiers(trades: list[dict[str, Any]]) -> dict[str, TierStats]:
    """Aggregate closed trades by confidence tier using money R."""
    buckets: dict[str, TierStats] = {name: TierStats() for name, _, _ in TIER_BINS}
    for row in trades:
        try:
            conf = float(row.get("confidence") if row.get("confidence") is not None else -1)
            pnl = float(row.get("pnl") or 0)
            rb = float(row.get("risk_budget") or 0)
        except (TypeError, ValueError):
            continue
        if conf < 0:
            continue
        r = money_r(pnl, rb)
        if r is None:
            continue
        buckets[tier_label(conf)].add(r)
    return buckets


def verdict_from_tiers(buckets: dict[str, TierStats]) -> tuple[str, bool]:
    """Return (verdict, tier_calibration_warning).

    top_tier_below_base when top exp < base exp and n_top >= MIN_TOP_N.
    """
    top = buckets.get("top_ge_0.88") or TierStats()
    base = buckets.get("base_lt_0.70") or TierStats()
    if top.n < MIN_TOP_N:
        return "insufficient_data", False
    if top.exp is None or base.exp is None or base.n < 1:
        return "insufficient_data", False
    if top.exp < base.exp:
        return "top_tier_below_base", True
    return "top_tier_ok", False


def run_audit(
    trades_path: Path | str = DEFAULT_TRADES_PATH,
    audit_path: Path | str = DEFAULT_AUDIT_PATH,
    now: datetime | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Read trades, aggregate tiers, write audit file. Skip if not due unless force."""
    trades_path = Path(trades_path)
    audit_path = Path(audit_path)
    now = now or datetime.now(tz=UTC)

    existing = _load_audit(audit_path)
    if not force and existing and not _is_due(existing, now, trades_path):
        return existing

    trades = _read_trades(trades_path)
    buckets = aggregate_tiers(trades)
    verdict, warning = verdict_from_tiers(buckets)
    result: dict[str, Any] = {
        "audited_at": now.astimezone(UTC).isoformat(),
        "next_run": (now.astimezone(UTC) + timedelta(days=AUDIT_INTERVAL_DAYS)).isoformat(),
        "tiers": {k: v.to_dict() for k, v in buckets.items()},
        "verdict": verdict,
        "tier_calibration_warning": warning,
        "min_top_n": MIN_TOP_N,
        "trades_path": str(trades_path),
        "n_trades_scanned": len(trades),
    }
    _save_audit(audit_path, result)
    if warning:
        top = buckets["top_ge_0.88"]
        base = buckets["base_lt_0.70"]
        logger.warning(
            "zeus tier audit: top_tier exp=%.4f (n=%s) BELOW base exp=%.4f (n=%s) — calibration warning",
            top.exp or 0,
            top.n,
            base.exp or 0,
            base.n,
        )
    else:
        logger.info("zeus tier audit: verdict=%s", verdict)
    return result


def status_line(audit: dict[str, Any] | None) -> str:
    """One-line TG status: санитар: верх яруса exp X (n=N) vs база Y — ок/хуже/мало данных."""
    if not audit:
        return "санитар: нет данных"
    tiers = audit.get("tiers") or {}
    top = tiers.get("top_ge_0.88") or {}
    base = tiers.get("base_lt_0.70") or {}
    top_exp = top.get("exp")
    base_exp = base.get("exp")
    top_n = int(top.get("n") or 0)
    verdict = audit.get("verdict") or "insufficient_data"
    label = {
        "top_tier_ok": "ок",
        "top_tier_below_base": "хуже",
        "insufficient_data": "мало данных",
    }.get(verdict, verdict)
    top_s = f"{top_exp:.3f}" if isinstance(top_exp, (int, float)) else "—"
    base_s = f"{base_exp:.3f}" if isinstance(base_exp, (int, float)) else "—"
    return f"санитар: верх яруса exp {top_s} (n={top_n}) vs база {base_s} — {label}"


def _is_due(existing: dict[str, Any], now: datetime, trades_path: Path) -> bool:
    """Due every 7 days OR when top tier first reaches MIN_TOP_N trades."""
    next_run = existing.get("next_run")
    if next_run:
        try:
            nr = datetime.fromisoformat(str(next_run).replace("Z", "+00:00"))
            if nr.tzinfo is None:
                nr = nr.replace(tzinfo=UTC)
            if now.astimezone(UTC) >= nr:
                return True
        except ValueError:
            return True
    # Early trigger: top tier n crossed MIN_TOP_N since last audit
    prev_top_n = int(((existing.get("tiers") or {}).get("top_ge_0.88") or {}).get("n") or 0)
    if prev_top_n < MIN_TOP_N:
        trades = _read_trades(trades_path)
        buckets = aggregate_tiers(trades)
        if buckets["top_ge_0.88"].n >= MIN_TOP_N:
            return True
    return False


def _read_trades(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError as exc:
        logger.warning("zeus_tier_audit: read trades failed: %s", exc)
    return out


def _load_audit(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception as exc:
        logger.warning("zeus_tier_audit: load failed: %s", exc)
        return None


def _save_audit(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("zeus_tier_audit: save failed: %s", exc)
