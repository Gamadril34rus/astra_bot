#!/usr/bin/env python3
"""Restore trading_engine.py from git show origin/master and apply sprint patches."""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "astra_bot" / "decision" / "trading_engine.py"


def main() -> None:
    raw = subprocess.check_output(
        ["git", "show", "origin/master:astra_bot/decision/trading_engine.py"],
        cwd=ROOT,
    )
    t = raw.decode("utf-8")
    t = t.replace("cfg.min_rr = 0.7", "cfg.min_rr = 3.0  # Sprint 2026-09-23", 1)

    anchor = "    entry_gate_max_costs_r: float = 0.25\n"
    insert = (
        "    entry_gate_max_costs_r: float = 0.25\n"
        "    max_trades_per_day: int = 6  # Sprint 2026-09-23\n"
        "    symbol_loss_cooldown_enabled: bool = True\n"
        "    htf_hard_gate_enabled: bool = False\n"
        "    disabled_strategy_names: frozenset[str] = frozenset({\n"
        '        "zscore_mean_reversion", "liquidity_sweep", "fair_value_gap",\n'
        '        "open_interest_divergence", "expanding_triangle", "ascending_triangle",\n'
        '        "breakout", "BreakoutStrategyV2", "ExpandingTriangleStrategy",\n'
        '        "AscendingTriangleStrategy",\n'
        "    })\n"
    )
    if "max_trades_per_day" not in t:
        if anchor not in t:
            raise SystemExit("anchor entry_gate_max_costs_r not found")
        t = t.replace(anchor, insert, 1)

    a = "        self.entry_gates = EntryGateConfig.from_engine_config(self.config)\n"
    b = (
        a
        + "        from ..core.kill_switch import SymbolLossGuard\n"
        + '        self.symbol_guard = SymbolLossGuard() if getattr(self.config, "symbol_loss_cooldown_enabled", True) else None\n'
        + "        self._daily_trade_count = 0\n"
        + '        self._trades_today_date = ""\n'
    )
    if "self.symbol_guard" not in t:
        if a not in t:
            raise SystemExit("entry_gates init anchor not found")
        t = t.replace(a, b, 1)

    m = "        if not primary:\n            return []\n"
    e = (
        "        if not primary:\n"
        "            return []\n"
        '        if getattr(self, "symbol_guard", None) is not None and self.symbol_guard.is_paused(symbol):\n'
        '            logger.info("SYMBOL_COOLDOWN skip %s", symbol)\n'
        "            return []\n"
        "        from datetime import datetime, timezone\n"
        '        _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")\n'
        '        if getattr(self, "_trades_today_date", "") != _today:\n'
        "            self._trades_today_date = _today\n"
        "            self._daily_trade_count = 0\n"
        '        if getattr(self, "_daily_trade_count", 0) >= int(getattr(self.config, "max_trades_per_day", 6)):\n'
        '            logger.info("DAILY_LIMIT reached (%d), skip %s", self._daily_trade_count, symbol)\n'
        "            return []\n"
    )
    if "DAILY_LIMIT reached" not in t:
        if m not in t:
            raise SystemExit("if not primary anchor not found")
        t = t.replace(m, e, 1)

    m2 = "        cand = decision.candidate\n"
    e2 = (
        "        cand = decision.candidate\n"
        '        _sname = str(getattr(cand, "strategy_name", "") or getattr(cand, "strategy", "") or "")\n'
        '        _dis = getattr(self.config, "disabled_strategy_names", frozenset())\n'
        "        if _sname and (_sname in _dis or any(d.lower() in _sname.lower() for d in _dis)):\n"
        '            logger.info("%s: strategy disabled by sprint denylist: %s", symbol, _sname)\n'
        "            return closed\n"
    )
    if "sprint denylist" not in t:
        if m2 not in t:
            raise SystemExit("cand = decision.candidate not found")
        t = t.replace(m2, e2, 1)

    marker = "            pos = self.broker.open_position(\n"
    inc = '            self._daily_trade_count = getattr(self, "_daily_trade_count", 0) + 1\n'
    if "self._daily_trade_count = getattr" not in t:
        if marker not in t:
            raise SystemExit("open_position marker not found")
        t = t.replace(marker, inc + marker, 1)

    ast.parse(t)
    if "class TradingEngine" not in t or "cfg.min_rr = 3.0" not in t:
        raise SystemExit("post-condition failed")
    OUT.write_text(t, encoding="utf-8")
    print("OK lines", len(t.splitlines()), "->", OUT)


if __name__ == "__main__":
    main()
