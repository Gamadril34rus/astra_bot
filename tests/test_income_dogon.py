"""Dogon / income sanity for live rockets (task №20)."""
from __future__ import annotations

import asyncio
import json

from astra_bot.decision.live_income_report import (
    aggregate_income,
    enqueue_pending,
    format_estimate_rocket,
    format_pending_rocket,
    format_rocket_correction,
    income_too_small,
    load_pending,
    plan_est_risk,
    resolve_pending_dogon,
)


def test_plan_est_risk():
    assert plan_est_risk(100.0, 90.0, 10.0) == 100.0
    assert plan_est_risk(None, 90.0, 10.0) is None


def test_income_too_small_threshold():
    est = 200.0
    small = aggregate_income([
        {"_incomeType": "REALIZED_PNL", "income": -5.0, "time": 1},
    ])
    assert income_too_small(small, est) is True
    big = aggregate_income([
        {"_incomeType": "REALIZED_PNL", "income": -50.0, "time": 1},
    ])
    assert income_too_small(big, est) is False
    assert income_too_small(small, 40.0) is False  # est <= 50


def test_format_no_question_mark():
    msg = format_pending_rocket("DOT-USDT", "long")
    assert "?" not in msg
    assert "уточняется" in msg
    corr = format_rocket_correction("DOT-USDT", "long", net=-120.5, reason="стоп")
    assert corr.startswith("уточнение: ")
    assert "-120.50$" in corr
    est = format_estimate_rocket("DOT-USDT", "short", est=80.0)
    assert "-80" not in est and "80.00$" not in est  # no fake dollars
    assert "UI" in est


def test_dogon_pending_then_confirm(tmp_path):
    pending_path = tmp_path / "pending.json"
    trades_path = tmp_path / "trades.jsonl"
    # seed exit pending_dogon
    trades_path.write_text(
        json.dumps({
            "event": "exit",
            "symbol": "DOT-USDT",
            "side": "long",
            "signal_id": "s1",
            "pnl": None,
            "pnl_source": "pending_dogon",
            "exit_reason": "стоп",
            "ts": 1000,
        })
        + "\n",
        encoding="utf-8",
    )
    enqueue_pending(
        {
            "symbol": "DOT-USDT",
            "side": "long",
            "signal_id": "s1",
            "since_ms": 1,
            "reason": "стоп",
            "est": 200.0,
            "ts": 1000.0,
        },
        path=pending_path,
    )

    class Client:
        def __init__(self):
            self.n = 0

        async def get_realized_income(self, symbol, start_ms=None, end_ms=None):
            self.n += 1
            if self.n == 1:
                return [{"_incomeType": "REALIZED_PNL", "income": -3.0, "time": 2}]
            return [
                {"_incomeType": "REALIZED_PNL", "income": -150.0, "time": 2},
                {"_incomeType": "TRADING_FEE", "income": -1.5, "time": 2},
            ]

    client = Client()
    sent: list[str] = []

    async def tg(msg):
        sent.append(msg)

    # first drain: too small vs 0.5*est=100 → stay pending
    n = asyncio.run(
        resolve_pending_dogon(
            client, tg, path=pending_path, trades_path=trades_path, now=1001.0
        )
    )
    assert n == 0
    assert len(load_pending(pending_path)) == 1

    # second: full income → correction + patch
    n = asyncio.run(
        resolve_pending_dogon(
            client, tg, path=pending_path, trades_path=trades_path, now=1002.0
        )
    )
    assert n == 1
    assert sent and sent[0].startswith("уточнение: ")
    assert load_pending(pending_path) == []
    row = json.loads(trades_path.read_text().splitlines()[0])
    assert row["pnl_source"] == "income_confirmed"
    assert row["pnl"] is not None


def test_dogon_24h_estimate(tmp_path):
    pending_path = tmp_path / "pending.json"
    trades_path = tmp_path / "trades.jsonl"
    trades_path.write_text("")
    enqueue_pending(
        {
            "symbol": "APT-USDT",
            "side": "short",
            "signal_id": "s2",
            "since_ms": 1,
            "reason": "стоп",
            "est": 90.0,
            "ts": 1.0,
        },
        path=pending_path,
    )

    class Client:
        async def get_realized_income(self, symbol, start_ms=None, end_ms=None):
            return []

    sent: list[str] = []

    async def tg(msg):
        sent.append(msg)

    n = asyncio.run(
        resolve_pending_dogon(
            Client(),
            tg,
            path=pending_path,
            trades_path=trades_path,
            now=1.0 + 25 * 3600,
        )
    )
    # Owner 31.10: TTL drops silently — no fake −1R TG
    assert n == 0
    assert sent == []
    assert load_pending(pending_path) == []


def test_backfill_correct_tiny(tmp_path):
    from astra_bot.adapters.bingx.income_backfill import correct_tiny_income_rows

    rows = [
        {
            "event": "entry",
            "symbol": "INJ-USDT",
            "signal_id": "x1",
            "entry": 20.0,
            "stop": 19.0,
            "qty": 100.0,
        },
        {
            "event": "exit",
            "symbol": "INJ-USDT",
            "signal_id": "x1",
            "entry": 20.0,
            "pnl": -3.0,
            "pnl_source": "income",
            "ts": 1_700_000_000,
        },
    ]
    # est = 100; tiny -3; income with full -80
    income = {
        "INJ-USDT": [
            {
                "_incomeType": "REALIZED_PNL",
                "income": -80.0,
                "time": 1_700_000_000_000,
            },
            {
                "_incomeType": "TRADING_FEE",
                "income": -0.5,
                "time": 1_700_000_000_000,
            },
        ]
    }
    out, n = correct_tiny_income_rows(rows, income, now=1_700_000_100)
    assert n == 1
    assert out[1]["pnl_source"] == "income_corrected"
    assert abs(out[1]["pnl"] - (-80.5)) < 1e-6


def test_stop_reason_marker():
    # pure convention unit: dogon reason defaults to стоп
    msg_e = format_estimate_rocket("X", "long", est=1.0, reason="стоп")
    assert "стоп" in msg_e
    assert "1.00$" not in msg_e
