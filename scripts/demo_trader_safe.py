#!/usr/bin/env python3
"""Safety wrapper around the market-aware Demo trader.

The base trader owns signal generation and execution. This wrapper adds
operational guardrails that must remain outside the strategy/model layer:
- hard Demo-only assertion;
- baseline/peak equity tracking;
- entry circuit breakers on daily loss and drawdown;
- readiness metrics updated from actual closed Demo trades;
- one-shot Telegram readiness notification.
"""

from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path

from demo_trader_pro import DemoTraderPro, Position
from astra_bot.core import readiness

GUARD_PATH = Path("models/risk_guard.json")
MAX_DRAWDOWN_PCT = Decimal("5.0")
MAX_DAILY_LOSS_PCT = Decimal("1.0")
MIN_MODEL_AUC = 0.55


class SafeDemoTrader(DemoTraderPro):
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
        await super().initialize()
        auc = float(getattr(getattr(self.model, "metrics", None), "roc_auc", 0.0) or 0.0)
        if auc < MIN_MODEL_AUC:
            raise RuntimeError(f"Model rejected for Demo: temporal ROC-AUC={auc:.3f} < {MIN_MODEL_AUC:.2f}")

        equity = await self.equity()
        if self.guard.get("baseline_equity") is None:
            self.guard["baseline_equity"] = float(equity)
        self.guard["peak_equity"] = max(float(self.guard.get("peak_equity") or 0.0), float(equity))
        self._save_guard()

    async def _risk_allows_entry(self) -> bool:
        if self.guard.get("halt_entries"):
            return False
        equity = await self.equity()
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
            state = readiness.record_day(
                trades=int(self.state.get("daily_trades", 0)),
                wins=int(self.state.get("daily_wins", 0)),
                pnl=float(self.state.get("daily_pnl", 0.0)),
                equity_end=float(await self.equity()),
            )
            if readiness.should_notify_ready():
                await self._send_ready_notification()

    async def _send_ready_notification(self) -> None:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        admin = os.getenv("TELEGRAM_ADMIN_ID", "").strip()
        if not token or not admin:
            return
        from telegram import Bot
        text = readiness.format_report()
        bot = Bot(token=token)
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
