"""Tests for live income TG reporting — exact sums, pending rocket, funding dedup."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from astra_bot.decision.live_income_report import (
    FUNDING_DUST,
    aggregate_income,
    claim_funding_event,
    enqueue_pending,
    format_funding_tg,
    format_pending_rocket,
    format_rocket,
    format_usd,
    load_pending,
    remove_pending,
)


def test_format_usd_sign_discipline():
    assert format_usd(None) is None
    assert format_usd(1.234) == "+1.23$"
    assert format_usd(-2.5) == "-2.50$"
    assert format_usd(0.0) == "0.00$"
    assert format_usd(0.001) == "0.00$"
    # never a lone plus/minus without digits
    assert format_usd(None) != "+?"
    assert "?" not in (format_usd(1.0) or "")


def test_aggregate_income_net_with_funding():
    rows = [
        {"_incomeType": "REALIZED_PNL", "income": "10.5"},
        {"_incomeType": "TRADING_FEE", "income": "-0.3"},
        {"_incomeType": "FUNDING_FEE", "income": "0.4"},
        {"_incomeType": "COMMISSION", "income": "-0.1"},
    ]
    agg = aggregate_income(rows)
    assert abs(agg["pnl"] - 10.5) < 1e-9
    assert abs(agg["fees"] - (-0.4)) < 1e-9
    assert abs(agg["funding"] - 0.4) < 1e-9
    assert abs(agg["net"] - 10.5) < 1e-9  # 10.5 - 0.4 + 0.4
    assert agg["has_realized"] is True


def test_aggregate_empty():
    agg = aggregate_income([])
    assert agg["net"] == 0.0
    assert agg["has_realized"] is False


def test_format_rocket_breakdown():
    msg = format_rocket("AAVE-USDT", "long", net=1.25, reason="тейк", funding=0.15, fees=-0.2)
    assert msg.startswith("🏁 AAVE ЛОНГ: закрыта +1.25$ нетто")
    assert "funding +0.15$" in msg
    assert "комиссии -0.20$" in msg
    assert "?" not in msg
    # negative net uses ASCII hyphen
    msg2 = format_rocket("SUI-USDT", "short", net=-3.5, reason="стоп", funding=-0.2, fees=-0.1)
    assert "закрыта -3.50$ нетто" in msg2
    assert "−" not in msg2  # no unicode minus


def test_format_pending_no_sign():
    msg = format_pending_rocket("BTC-USDT", "short")
    assert "🏁 BTC ШОРТ: закрыта — сумма уточняется, пришлю следом" == msg
    assert "+" not in msg
    assert "?" not in msg


def test_pending_queue_roundtrip(tmp_path):
    path = tmp_path / "pending.json"
    enqueue_pending(
        {"symbol": "ETH-USDT", "side": "long", "signal_id": "s1", "since_ms": 1000},
        path=path,
    )
    items = load_pending(path)
    assert len(items) == 1
    assert items[0]["symbol"] == "ETH-USDT"
    # re-enqueue same key replaces
    enqueue_pending(
        {"symbol": "ETH-USDT", "side": "long", "signal_id": "s1", "since_ms": 1000, "note": "x"},
        path=path,
    )
    items = load_pending(path)
    assert len(items) == 1
    assert items[0].get("note") == "x"
    remove_pending("ETH-USDT", "s1", path=path)
    assert load_pending(path) == []


def test_funding_dust_and_format():
    assert format_funding_tg("BTC-USDT", "short", 0.05) is None
    assert format_funding_tg("BTC-USDT", "short", FUNDING_DUST - 0.01) is None
    msg = format_funding_tg("BTC-USDT", "short", 0.25)
    assert msg is not None
    assert msg.startswith("💰 BTC ШОРТ: funding +0.25$")
    assert "лонг платит шорту" in msg
    msg2 = format_funding_tg("ETH-USDT", "long", -0.5)
    assert msg2 is not None
    assert "funding -0.50$" in msg2
    assert "мы платим шорту" in msg2


def test_funding_dedup(tmp_path):
    path = tmp_path / "fund.json"
    assert claim_funding_event("BTC-USDT", 1234567890, 0.3, path=path) is True
    assert claim_funding_event("BTC-USDT", 1234567890, 0.3, path=path) is False
    assert claim_funding_event("BTC-USDT", 1234567891, 0.3, path=path) is True


def test_pending_then_exact_flow(tmp_path):
    """Simulate: no income → pending rocket text; later aggregate → exact rocket."""
    path = tmp_path / "pending.json"
    first = format_pending_rocket("AAVE-USDT", "long")
    assert "уточняется" in first
    enqueue_pending(
        {
            "symbol": "AAVE-USDT",
            "side": "long",
            "signal_id": "sig-a",
            "since_ms": 1,
            "reason": "тейк",
        },
        path=path,
    )
    # synthetic income arrives
    rows = [
        {"_incomeType": "REALIZED_PNL", "income": 2.0},
        {"_incomeType": "TRADING_FEE", "income": -0.05},
        {"_incomeType": "FUNDING_FEE", "income": 0.12},
    ]
    agg = aggregate_income(rows)
    follow = format_rocket(
        "AAVE-USDT",
        "long",
        net=agg["net"],
        reason="тейк",
        funding=agg["funding"],
        fees=agg["fees"],
    )
    assert "+2.07$" in follow or format_usd(agg["net"]) in follow
    assert "?" not in follow
    remove_pending("AAVE-USDT", "sig-a", path=path)
    assert load_pending(path) == []
