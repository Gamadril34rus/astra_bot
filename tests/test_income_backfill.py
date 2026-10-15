"""Income backfill: match rules for null-pnl live exits (audit 438)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from astra_bot.adapters.bingx.income_backfill import (
    apply_backfill_to_rows,
    load_trades,
    match_income_for_exit,
    run_income_backfill,
    save_trades,
)


def _inc(ts: float, pnl: float, *, qty: float | None = None) -> dict:
    row = {
        "incomeType": "REALIZED_PNL",
        "_incomeType": "REALIZED_PNL",
        "income": str(pnl),
        "time": int(ts * 1000),
    }
    if qty is not None:
        row["qty"] = qty
    return row


def test_exchange_flat_match() -> None:
    ts = time.time() - 100
    exit_row = {
        "event": "exit",
        "symbol": "AVAX-USDT",
        "side": "long",
        "pnl": None,
        "exit_reason": "exchange_flat",
        "ts": ts,
    }
    income = [_inc(ts + 10, -1.25)]
    pnl, status = match_income_for_exit(exit_row, income)
    assert status == "matched"
    assert pnl is not None and abs(pnl - (-1.25)) < 1e-9


def test_no_fills_single_candidate() -> None:
    ts = time.time()
    close_ts = ts - 5 * 3600
    exit_row = {
        "event": "exit",
        "symbol": "XRP-USDT",
        "side": "short",
        "pnl": None,
        "exit_reason": "no_fills",
        "ts": ts,
    }
    income = [_inc(close_ts, -0.88)]
    pnl, status = match_income_for_exit(exit_row, income)
    assert status == "matched"
    assert abs(pnl - (-0.88)) < 1e-9


def test_no_fills_two_candidates_unresolved() -> None:
    ts = time.time()
    exit_row = {
        "event": "exit",
        "symbol": "ETH-USDT",
        "side": "short",
        "pnl": None,
        "exit_reason": "no_fills",
        "ts": ts,
    }
    income = [
        _inc(ts - 3600, -1.0),
        _inc(ts - 7200, -2.0),
    ]
    pnl, status = match_income_for_exit(exit_row, income)
    assert status == "unresolved"
    assert pnl is None


def test_no_fills_two_candidates_qty_match() -> None:
    ts = time.time()
    exit_row = {
        "event": "exit",
        "symbol": "ETH-USDT",
        "side": "short",
        "pnl": None,
        "exit_reason": "no_fills",
        "ts": ts,
        "qty": 0.049,
    }
    income = [
        _inc(ts - 3600, -1.0, qty=0.1),
        _inc(ts - 7200, -2.0, qty=0.049),
    ]
    pnl, status = match_income_for_exit(exit_row, income)
    assert status == "matched"
    assert abs(pnl - (-2.0)) < 1e-9


def test_existing_pnl_untouched() -> None:
    rows = [
        {
            "event": "exit",
            "symbol": "DOGE-USDT",
            "side": "short",
            "pnl": -1.0974,
            "exit_reason": "income",
            "ts": time.time() - 1000,
        }
    ]
    income_by = {"DOGE-USDT": [_inc(time.time() - 1000, -9.9)]}
    out, n = apply_backfill_to_rows(rows, income_by)
    assert n == 0
    assert out[0]["pnl"] == -1.0974
    assert "pnl_source" not in out[0]


def test_idempotent_second_pass(tmp_path: Path) -> None:
    path = tmp_path / "zeus_live_trades.jsonl"
    ts = time.time() - 50
    rows = [
        {
            "event": "exit",
            "symbol": "BCH-USDT",
            "side": "short",
            "pnl": None,
            "exit_reason": "exchange_flat",
            "ts": ts,
        }
    ]
    save_trades(rows, path)
    income_by = {"BCH-USDT": [_inc(ts, -1.5)]}
    out1, n1 = apply_backfill_to_rows(load_trades(path), income_by, now=ts)
    assert n1 == 1
    save_trades(out1, path)
    out2, n2 = apply_backfill_to_rows(load_trades(path), income_by, now=ts + 10)
    assert n2 == 0
    assert out2[0]["pnl"] == out1[0]["pnl"]
    assert out2[0].get("pnl_source") == "income_backfill"


@pytest.mark.asyncio
async def test_run_income_backfill_async(tmp_path: Path) -> None:
    path = tmp_path / "zeus_live_trades.jsonl"
    ts = time.time() - 20
    save_trades(
        [
            {
                "event": "exit",
                "symbol": "AVAX-USDT",
                "side": "long",
                "pnl": None,
                "exit_reason": "exchange_flat",
                "ts": ts,
            }
        ],
        path,
    )

    class FakeClient:
        async def get_realized_income(self, symbol, *, start_ms=None, end_ms=None):
            return [_inc(ts, -0.42)]

    stats = await run_income_backfill(FakeClient(), path=path, now=ts + 1)
    assert stats["updated"] == 1
    loaded = load_trades(path)
    assert loaded[0]["pnl"] == -0.42
    assert loaded[0]["pnl_source"] == "income_backfill"
