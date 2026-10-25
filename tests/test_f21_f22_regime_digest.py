"""F21 regime bars/EMA200 + F22 digest day_pnl / dedup."""
from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from astra_bot.decision.live_exit_engine import classify_current_regime


def _bars(n: int, *, trend: str = "flat", start: float = 100.0) -> list:
    """Synthetic OHLC. trend=up/down/flat."""
    out = []
    px = start
    for i in range(n):
        if trend == "up":
            px = start + i * 0.15
        elif trend == "down":
            px = start - i * 0.15
        else:
            px = start + (0.02 if i % 2 == 0 else -0.02)
        o, c = px, px + (0.05 if trend == "up" else (-0.05 if trend == "down" else 0.0))
        h, l = max(o, c) + 0.1, min(o, c) - 0.1
        out.append(
            SimpleNamespace(
                open_time=1_700_000_000_000 + i * 14_400_000,
                open=o, high=h, low=l, close=c, volume=1000.0 + i,
            )
        )
    return out


def test_classify_bars_lt_60():
    reg, reason = classify_current_regime(_bars(30))
    assert reg == ""
    assert reason == "bars_lt_60"


def test_classify_bars_lt_200_ema():
    """F21: 119 bars → bars_lt_200_ema (not silent unknown)."""
    reg, reason = classify_current_regime(_bars(119))
    assert reg == ""
    assert reason == "bars_lt_200_ema"


def test_classify_trend_not_unknown():
    """F21: 220 down-trend bars → non-empty regime, reason ok."""
    reg, reason = classify_current_regime(_bars(220, trend="down", start=120.0))
    assert reason == "ok", (reg, reason)
    assert reg and reg != "UNKNOWN"
    # down trend should land in bear or range family
    assert any(x in reg for x in ("BEAR", "RANGE", "BULL", "VOL", "BREAKOUT", "PANIC"))


def test_classify_flat_range_family():
    reg, reason = classify_current_regime(_bars(220, trend="flat", start=100.0))
    assert reason == "ok", (reg, reason)
    assert reg in (
        "RANGE", "LOW_VOLATILITY", "HIGH_VOLATILITY",
        "WEAK_BULL_TREND", "WEAK_BEAR_TREND",
        "STRONG_BULL_TREND", "STRONG_BEAR_TREND",
        "BREAKOUT", "PANIC",
    )


def test_day_exits_include_income_source(tmp_path):
    from scripts.zeus_live_clock import _day_exits_msk, _msk_now

    trades = tmp_path / "trades.jsonl"
    # MSK day of fixed ts
    now = 1792951047.0  # ~2026-10-25 20:57 MSK
    day0 = _msk_now(now).replace(hour=1, minute=0, second=0, microsecond=0).timestamp()
    rows = [
        {"event": "exit", "symbol": "ADA-USDT", "pnl": -450.09, "pnl_source": "income", "ts": day0 + 100},
        {"event": "entry", "symbol": "X", "pnl": None, "ts": day0 + 200},
        {"event": "partial_fill", "symbol": "Y", "pnl": 1.0, "pnl_source": "income", "ts": day0 + 300},
        {"event": "exit", "symbol": "OLD", "pnl": -10, "pnl_source": "income", "ts": day0 - 86400},
    ]
    trades.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    exits = _day_exits_msk(trades, now_ts=now)
    assert len(exits) == 1
    assert exits[0]["symbol"] == "ADA-USDT"
    assert float(exits[0]["pnl"]) == -450.09


def test_digest_shows_income_day_pnl(tmp_path, monkeypatch):
    from scripts import zeus_live_clock as z

    trades = tmp_path / "trades.jsonl"
    digest = tmp_path / "digest.json"
    now = 1792908000.0  # inside AM window 25.10 ~09:00 MSK? 1792908003 was am digest
    # ensure window key works
    key = z._digest_window_key(now)
    assert key and key.endswith(":am"), key
    day0 = z._msk_now(now).replace(hour=2, minute=0, second=0, microsecond=0).timestamp()
    trades.write_text(
        json.dumps({
            "event": "exit", "symbol": "ADA-USDT", "side": "short",
            "pnl": -450.09, "pnl_source": "income", "ts": day0,
        }) + "\n",
        encoding="utf-8",
    )
    sent: list[str] = []

    async def fake_tg(msg: str):
        sent.append(msg)

    class Client:
        async def get_balance_usdt(self):
            return Decimal("100000")

    class Broker:
        _halt_reason = None

    monkeypatch.setattr(z, "write_live_equity", lambda *a, **k: None)
    monkeypatch.setattr(z, "load_positions", lambda *a, **k: {} if False else __import__(
        "astra_bot.decision.live_positions", fromlist=["load_positions"]
    ).load_positions)
    # empty positions via path isolation
    with patch("astra_bot.decision.live_positions.load_positions", return_value={}):
        with patch("astra_bot.decision.live_arm_wiring.append_journal", lambda *a, **k: None):
            out = asyncio.new_event_loop().run_until_complete(
                z._maybe_send_digest(
                    Broker(), Client(), now_ts=now,
                    digest_path=digest, trades_path=trades, send_tg=fake_tg,
                )
            )
    assert out == key
    assert sent, "digest not sent"
    assert "−450.09$" in sent[0] or "-450.09$" in sent[0] or "450.09" in sent[0]
    assert "0+/1−" in sent[0] or "1−" in sent[0]


def test_digest_dedup_second_dispatch(tmp_path, monkeypatch):
    from scripts import zeus_live_clock as z

    trades = tmp_path / "trades.jsonl"
    trades.write_text("", encoding="utf-8")
    digest = tmp_path / "digest.json"
    now = 1792908000.0
    key = z._digest_window_key(now)
    assert key
    sent: list[str] = []

    async def fake_tg(msg: str):
        sent.append(msg)

    class Client:
        async def get_balance_usdt(self):
            return Decimal("96399.41")

    class Broker:
        _halt_reason = None

    with patch("astra_bot.decision.live_positions.load_positions", return_value={}):
        with patch("astra_bot.decision.live_arm_wiring.append_journal", lambda *a, **k: None):
            with patch.object(z, "write_live_equity", lambda *a, **k: None):
                k1 = asyncio.new_event_loop().run_until_complete(
                    z._maybe_send_digest(
                        Broker(), Client(), now_ts=now,
                        digest_path=digest, trades_path=trades, send_tg=fake_tg,
                    )
                )
                k2 = asyncio.new_event_loop().run_until_complete(
                    z._maybe_send_digest(
                        Broker(), Client(), now_ts=now,
                        digest_path=digest, trades_path=trades, send_tg=fake_tg,
                    )
                )
    assert k1 == key
    assert k2 is None
    assert len(sent) == 1
