"""Append-only double-entry trade ledger (TZ P1.8).

Every cash/position mutation is a JSONL event. Replay reconstructs
cash and positions without reading PaperBroker in-memory state.

Schema version is stored on every row. Events are never rewritten
in place — rotation moves old rows to ``*.archive.jsonl``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

LEDGER_SCHEMA_VERSION = 1
DEFAULT_LEDGER_PATH = Path("models") / "paper_ledger.jsonl"

LedgerKind = Literal[
    "order",
    "fill",
    "fee",
    "partial_fill",
    "cancel",
    "rejection",
    "reconciliation",
]


def _dec(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


@dataclass
class LedgerEvent:
    """One immutable ledger row."""

    kind: str
    account: str = "paper"
    symbol: str = ""
    side: str = ""
    qty: str = "0"
    price: str = "0"
    fee: str = "0"
    fee_asset: str = "USDT"
    pnl: str = "0"
    cash_delta: str = "0"
    position_delta: str = "0"
    ref_id: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    schema_version: int = LEDGER_SCHEMA_VERSION
    event_id: str = ""
    ts_ms: int = 0

    def __post_init__(self) -> None:
        if not self.event_id:
            self.event_id = uuid.uuid4().hex
        if not self.ts_ms:
            self.ts_ms = _now_ms()
        if self.kind not in {
            "order",
            "fill",
            "fee",
            "partial_fill",
            "cancel",
            "rejection",
            "reconciliation",
        }:
            raise ValueError(f"unknown ledger kind: {self.kind}")

    def to_json(self) -> str:
        payload = asdict(self)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, line: str) -> LedgerEvent:
        data = json.loads(line)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class LedgerSnapshot:
    cash: Decimal
    positions: dict[str, Decimal]
    realized_pnl: Decimal
    fees: Decimal
    events: int


class TradeLedger:
    """Append-only JSONL ledger with replay."""

    def __init__(self, path: Path | str | None = None, *, initial_cash: Decimal = Decimal("0")):
        self.path = Path(path) if path else Path(
            os.environ.get("ASTRA_LEDGER_PATH", str(DEFAULT_LEDGER_PATH))
        )
        self.initial_cash = initial_cash
        self._lock = threading.Lock()

    def append(self, event: LedgerEvent) -> LedgerEvent:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = event.to_json()
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
        return event

    def record(
        self,
        kind: str,
        *,
        account: str = "paper",
        symbol: str = "",
        side: str = "",
        qty: Decimal | str | float = 0,
        price: Decimal | str | float = 0,
        fee: Decimal | str | float = 0,
        pnl: Decimal | str | float = 0,
        cash_delta: Decimal | str | float = 0,
        position_delta: Decimal | str | float = 0,
        ref_id: str = "",
        meta: dict[str, Any] | None = None,
    ) -> LedgerEvent:
        event = LedgerEvent(
            kind=kind,
            account=account,
            symbol=symbol,
            side=side,
            qty=str(_dec(qty)),
            price=str(_dec(price)),
            fee=str(_dec(fee)),
            pnl=str(_dec(pnl)),
            cash_delta=str(_dec(cash_delta)),
            position_delta=str(_dec(position_delta)),
            ref_id=ref_id,
            meta=meta or {},
        )
        return self.append(event)

    def iter_events(self) -> Iterable[LedgerEvent]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield LedgerEvent.from_json(line)
                except Exception as exc:
                    logger.warning("skipping corrupt ledger row: %s", exc)

    def replay(self, *, initial_cash: Decimal | None = None) -> LedgerSnapshot:
        cash = initial_cash if initial_cash is not None else self.initial_cash
        positions: dict[str, Decimal] = {}
        realized = Decimal("0")
        fees = Decimal("0")
        n = 0
        for event in self.iter_events():
            n += 1
            cash += _dec(event.cash_delta)
            fees += _dec(event.fee)
            realized += _dec(event.pnl)
            if event.symbol:
                delta = _dec(event.position_delta)
                if delta:
                    positions[event.symbol] = positions.get(event.symbol, Decimal("0")) + delta
                    if positions[event.symbol] == 0:
                        positions.pop(event.symbol, None)
        return LedgerSnapshot(
            cash=cash,
            positions=positions,
            realized_pnl=realized,
            fees=fees,
            events=n,
        )


_LEDGER: TradeLedger | None = None
_LEDGER_LOCK = threading.Lock()


def get_ledger() -> TradeLedger:
    global _LEDGER
    with _LEDGER_LOCK:
        if _LEDGER is None:
            _LEDGER = TradeLedger()
        return _LEDGER


def reset_ledger(path: Path | str | None = None, *, initial_cash: Decimal = Decimal("0")) -> TradeLedger:
    global _LEDGER
    with _LEDGER_LOCK:
        _LEDGER = TradeLedger(path, initial_cash=initial_cash)
        return _LEDGER


def record_best_effort(**kwargs: Any) -> None:
    """Never raise into the trading path."""
    try:
        get_ledger().record(**kwargs)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("ledger write skipped: %s", exc)
