#!/usr/bin/env python3
"""Safety wrapper around the market-aware virtual paper trader."""

from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path

from demo_trader_pro import DemoTraderPro, Position
from astra_bot.core import readiness
from astra_bot.core.instruments import TRADING_UNIVERSE
from astra_bot.ml.model_trainer import MLModel

GUARD_PATH = Path("models/risk_guard.json")
MODEL_PATH = Path("models/current.pkl")
LESSONS_PATH = Path("models/lessons.jsonl")
MAX_DRAWDOWN_PCT = Decimal("5.0")
MAX_DAILY_LOSS_PCT = Decimal("1.0")
MIN_MODEL_AUC = 0.55


class SafeDemoTrader(DemoTraderPro):
    """Operational safety layer for a strictly virtual trader.

    It deliberately does not call the base initialize() instrument parser because
    malformed optional OKX instrument metadata must never prevent paper trading.
    The worker only needs the live symbol IDs and state.
    """

    def __init__(self) -> None:
        super().__init__()
        self.guard = self._load_guard()

    def _load_guard(self) -> dict:
        if not GUARD_PATH.exists():
            return {"baseline_equity": None, "peak_equity": None, "halt_entries": False, "halt_reason": ""}
        try:
            return json.loads(GUARD_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {"baseline_equity": None, "peak_equity": None, "halt_entries": False, "halt_reason": ""}

    def _save_guard(self) -> None:
        GUARD_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = GUARD_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.guard, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(GUARD_PATH)

    async def initialize(self) -> None:
        if os.getenv("OKX_DEMO", "1").lower() in {"0", "false", "no"}:
            raise RuntimeError("OKX_DEMO must remain enabled")
        if not os.getenv("OKX_API_KEY") or not os.getenv("OKX_API_SECRET"):
            raise RuntimeError("OKX demo credentials are missing")

        await self.client.initialize()

        # Read only the fields needed by the paper worker. Do not parse optional
        # numeric instrument metadata such as minSz/last, which can be empty.
        raw = await self.client._request(
            "GET",
            "/api/v5/public/instruments",
            params={"instType": "SPOT"},
            signed=False,
        )
        live = {
            str(item.get("instId", "")).replace("-", "/")
            for item in raw
            if item.get("state", "") in {"live", "trading"}
        }
        filtered = tuple(symbol for symbol in TRADING_UNIVERSE if symbol in live)
        if not filtered:
            raise RuntimeError("OKX returned no usable SPOT instruments from ASTRA universe")
        self.active_symbols = filtered
        missing = sorted(set(TRADING_UNIVERSE) - set(filtered))
        self.state["active_symbols"] = list(filtered)
        import logging
        log = logging.getLogger("demo_trader_pro")
        log.info("OKX SPOT universe: %d/%d active", len(filtered), len(TRADING_UNIVERSE))
        if missing:
            log.warning("Excluded unavailable instruments: %s", ", ".join(missing))

        for symbol, raw_position in self.state.get("positions", {}).items():
            try:
                self.positions[symbol] = Position(**raw_position)
            except Exception:
                log.warning("Ignoring malformed persisted position: %s", symbol)

        if not MODEL_PATH.exists():
            raise RuntimeError("models/current.pkl is missing; run historical training first")
        self.model = MLModel.load(str(MODEL_PATH))
        if not self.model.is_fitted:
            raise RuntimeError("models/current.pkl is not fitted")
        if not LESSONS_PATH.exists() or LESSONS_PATH.stat().st_size == 0:
            raise RuntimeError("models/lessons.jsonl is missing or empty")

        log.info(
            "model=%s features=%d paper_equity=%.2f",
            self.model.version,
            len(self.model.feature_names),
            self.paper_equity(),
        )

        auc = float(getattr(getattr(self.model, "metrics", None), "roc_auc", 0.0) or 0.0)
        if auc < MIN_MODEL_AUC:
            raise RuntimeError(f"Model rejected for Demo: temporal ROC-AUC={auc:.3f} < {MIN_MODEL_AUC:.2f}")

        # Demo equity is local virtual capital. Never query exchange balance for it.
        equity = Decimal(str(self.paper_equity()))
        if self.guard.get("baseline_equity") is None:
            self.guard["baseline_equity"] = float(equity)
        self.guard["peak_equity"] = max(float(self.guard.get("peak_equity") or 0.0), float(equity))
        self._save_guard()
        self.save_state()

    async def _risk_allows_entry(self) -> bool:
        if self.guard.get("halt_entries"):
            return False
        equity = Decimal(str(self.paper_equity()))
        baseline = Decimal(str(self.guard.get("baseline_equity") or equity))
        peak = Decimal(str(self.guard.get("peak_equity") or equity))
        peak = max(peak, equity)
        self.guard["peak_equity"] = float(peak)

        drawdown = (peak - equity) / peak * 100 if peak > 0 else Decimal("0")
        daily_pnl = Decimal(str(self.state.get("daily_pnl", 0.0)))
        daily_limit = baseline * MAX_DAILY_LOSS_PCT / 100 if baseline > 0 else Decimal("0")

        if drawdown >= MAX_DRAWDOWN_PCT:
            self.guard["halt_entries"] = True
            self.guard["halt_reason"] = f"drawdown {drawdown:.2f}% >= {MAX_DRAWDOWN_PCT}%"
        elif daily_pnl <= -daily_limit:
            self.guard["halt_entries"] = True
            self.guard["halt_reason"] = f"daily loss {daily_pnl:.2f} <= {-daily_limit:.2f}"
        self._save_guard()
        return not self.guard.get("halt_entries")

    async def open_position(self, symbol: str, features: dict[str, float], price: float, score: float, probability: float, allocated: Decimal) -> None:
        if not await self._risk_allows_entry():
            return
        await super().open_position(symbol, features, price, score, probability, allocated)

    async def close_position(self, pos: Position, price: float, reason: str) -> None:
        before = int(self.state.get("total_trades", 0))
        await super().close_position(pos, price, reason)
        if int(self.state.get("total_trades", 0)) > before:
            readiness.record_day(
                trades=int(self.state.get("daily_trades", 0)),
                wins=int(self.state.get("daily_wins", 0)),
                pnl=float(self.state.get("daily_pnl", 0.0)),
                equity_end=float(self.paper_equity()),
            )
            if readiness.should_notify_ready():
                await self._send_ready_notification()

    async def _send_ready_notification(self) -> None:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        admin = os.getenv("TELEGRAM_ADMIN_ID", "").strip()
        if not token or not admin:
            return
        from telegram import Bot
        bot = Bot(token=token)
        text = readiness.format_report()
        for raw_id in admin.split(","):
            raw_id = raw_id.strip()
            if raw_id:
                await bot.send_message(chat_id=int(raw_id), text=text)


async def main() -> None:
    trader = SafeDemoTrader()
    try:
        await trader.run()
    finally:
        await trader.client.close()


if __name__ == "__main__":
    asyncio.run(main())
