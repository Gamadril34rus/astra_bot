"""Tests for live income TG reporting — exact sums, pending rocket, funding dedup."""
from __future__ import annotations

import asyncio
import json

import pytest
from astra_bot.decision.live_income_report import (
    aggregate_income,
    claim_funding_event,
    enqueue_pending,
    fetch_income_with_retries,
    filter_income_rows_for_position_side,
    format_pending_rocket,
    format_rocket,
    format_usd,
    load_pending,
    remove_pending,
    resolve_pending_dogon,
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
    assert msg == "🏁 BTC ШОРТ: закрыта — сумма уточняется, пришлю следом"
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


def test_funding_day_accumulate_and_reset(tmp_path, monkeypatch):
    """New funding event adds to day sum; MSK date change resets."""
    import asyncio

    from scripts import zeus_live_clock as clock

    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir(parents=True)
    # empty positions → accumulate returns 0 for new day
    pos = tmp_path / "models" / "zeus_live_positions.json"
    pos.write_text(json.dumps({"positions": {}, "ts": 0}), encoding="utf-8")

    class _C:
        async def get_realized_income(self, symbol, start_ms=None):
            return []

    async def _run():
        s = await clock._accumulate_funding_day(_C())
        assert s == 0.0
        day_path = tmp_path / "models" / "zeus_funding_day.json"
        assert day_path.exists()
        raw = json.loads(day_path.read_text(encoding="utf-8"))
        assert "date" in raw and raw["sum"] == 0.0
        # inject sum and claim-like manual
        raw["sum"] = 1.25
        day_path.write_text(json.dumps(raw), encoding="utf-8")
        s2 = await clock._accumulate_funding_day(_C())
        assert abs(s2 - 1.25) < 1e-9  # same day, no new events

        # day rollover
        raw["date"] = "2000-01-01"
        raw["sum"] = 9.99
        day_path.write_text(json.dumps(raw), encoding="utf-8")
        s3 = await clock._accumulate_funding_day(_C())
        assert abs(s3 - 0.0) < 1e-9  # reset

    asyncio.run(_run())


def test_funding_day_new_event_adds(tmp_path, monkeypatch):
    import asyncio

    from scripts import zeus_live_clock as clock

    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir(parents=True)
    pos = tmp_path / "models" / "zeus_live_positions.json"
    pos.write_text(
        json.dumps({
            "positions": {
                "BTC-USDT|short": {"symbol": "BTC-USDT", "side": "short", "qty": "1"}
            },
            "ts": 0,
        }),
        encoding="utf-8",
    )

    class _C:
        async def get_realized_income(self, symbol, start_ms=None):
            return [
                {"_incomeType": "FUNDING_FEE", "income": "0.50", "time": 1_700_000_000_000},
            ]

    async def _run():
        s = await clock._accumulate_funding_day(_C())
        assert abs(s - 0.50) < 1e-9
        # second call dedups
        s2 = await clock._accumulate_funding_day(_C())
        assert abs(s2 - 0.50) < 1e-9

    asyncio.run(_run())



def test_income_rows_require_position_side_or_matched_fill_trade_id():
    rows = [
        {
            "incomeType": "REALIZED_PNL",
            "income": "10.00",
            "tradeId": "long-fill",
        },
        {
            "incomeType": "REALIZED_PNL",
            "income": "-50.00",
            "tradeId": "short-fill",
            "positionSide": "SHORT",
        },
        {
            "incomeType": "FUNDING_FEE",
            "income": "-0.25",
            "time": 1_700_000_000_000,
        },
    ]
    selected, ambiguous = filter_income_rows_for_position_side(
        rows, "long", trade_ids={"long-fill"}
    )
    assert ambiguous is False
    assert len(selected) == 1
    assert selected[0]["tradeId"] == "long-fill"


def test_income_pnl_without_side_or_linked_fill_id_is_ambiguous():
    rows = [
        {
            "incomeType": "REALIZED_PNL",
            "income": "25.00",
            "time": 1_700_000_000_000,
        }
    ]
    selected, ambiguous = filter_income_rows_for_position_side(
        rows, "long", trade_ids=set()
    )
    assert selected == []
    assert ambiguous is True


def test_income_retry_stops_after_ambiguous_hedge_attribution():
    class Client:
        def __init__(self):
            self.calls = 0
            self.income_endpoint_cooldown_active = False

        async def get_realized_income(self, symbol, start_ms=None):
            self.calls += 1
            return [
                {
                    "incomeType": "REALIZED_PNL",
                    "income": "25.00",
                    "time": 1_700_000_000_000,
                }
            ]

    client = Client()
    result = asyncio.run(
        fetch_income_with_retries(
            client,
            "BTC-USDT",
            1,
            attempts=3,
            delay_sec=0,
            position_side="long",
            trade_ids=set(),
        )
    )
    assert client.calls == 1
    assert result["ambiguous_position_side"] is True
    assert result["has_realized"] is False


def test_pending_dogon_keeps_ambiguous_income_without_patching_or_tg(tmp_path):
    pending_path = tmp_path / "pending.json"
    trades_path = tmp_path / "trades.jsonl"
    enqueue_pending(
        {
            "symbol": "BTC-USDT",
            "side": "long",
            "signal_id": "signal-long",
            "since_ms": 1,
            "reason": "стоп",
            "entry": 100.0,
            "trade_ids": [],
            "ts": 1_700_000_000.0,
        },
        path=pending_path,
    )

    class Client:
        async def get_realized_income(self, symbol, start_ms=None):
            return [
                {
                    "incomeType": "REALIZED_PNL",
                    "income": "40.00",
                    "time": 1_700_000_001_000,
                }
            ]

    sent = []
    result = asyncio.run(
        resolve_pending_dogon(
            Client(),
            send_tg=lambda message: sent.append(message),
            path=pending_path,
            trades_path=trades_path,
            now=1_700_000_100.0,
        )
    )
    assert result == 0
    assert sent == []
    assert len(load_pending(pending_path)) == 1
    assert not trades_path.exists()


def test_match_position_history_requires_unique_symbol_side_and_open_time():
    from astra_bot.decision.live_income_report import match_position_history

    row = {
        "symbol": "BTC-USDT",
        "positionSide": "LONG",
        "openTime": 1_700_000_000_000,
        "updateTime": 1_700_000_030_000,
        "avgClosePrice": "99.5",
        "netProfit": "-2.2",
        "positionCommission": "-0.1",
        "totalFunding": "-0.1",
        "positionId": "position-1",
    }
    matched = match_position_history(
        [row],
        symbol="BTC-USDT",
        side="long",
        target_open_ms=1_700_000_000_000,
    )
    assert matched is not None
    assert matched["net_pnl"] == -2.2
    assert matched["pnl"] == -2.1
    assert matched["fees"] == -0.1
    assert matched["funding"] == -0.1
    assert matched["exit_price"] == 99.5
    assert matched["position_id"] == "position-1"

    assert match_position_history(
        [row],
        symbol="BTC-USDT",
        side="short",
        target_open_ms=1_700_000_000_000,
    ) is None
    assert match_position_history(
        [row, dict(row, positionId="position-2")],
        symbol="BTC-USDT",
        side="long",
        target_open_ms=1_700_000_000_000,
    ) is None


def test_pending_dogon_resolves_ambiguous_income_from_position_history(tmp_path, monkeypatch):
    from astra_bot.decision import live_income_report as income_report

    pending_path = tmp_path / "pending.json"
    trades_path = tmp_path / "trades.jsonl"
    opened_at_ms = 1_700_000_000_000
    # Legacy plan timestamp was populated hours after the actual entry.
    since_ms = opened_at_ms + 3 * 60 * 60 * 1000
    enqueue_pending(
        {
            "symbol": "BTC-USDT",
            "side": "long",
            "signal_id": "signal-long",
            "since_ms": since_ms,
            "reason": "выход",
            "entry": 100.0,
            "qty": 1.0,
            "leverage": 3.0,
            "stop": 98.0,
            "target": 105.0,
            "trade_ids": [],
            "ts": 1_700_000_000.0,
        },
        path=pending_path,
    )
    entry_row = {
        "event": "entry",
        "symbol": "BTC-USDT",
        "side": "long",
        "signal_id": "signal-long",
        "ts": opened_at_ms / 1000.0,
        "entry": 100.0,
        "qty": 1.0,
        "leverage": 3.0,
    }
    exit_row = {
        "event": "exit",
        "symbol": "BTC-USDT",
        "side": "long",
        "signal_id": "signal-long",
        "entry": 100.0,
        "exit": None,
        "pnl": None,
        "pnl_source": "pending_dogon",
    }
    trades_path.write_text(
        json.dumps(entry_row) + "\n" + json.dumps(exit_row) + "\n",
        encoding="utf-8",
    )

    class Client:
        def __init__(self):
            self.history_calls = 0
            self.income_start_ms = None

        async def get_realized_income(self, symbol, start_ms=None):
            self.income_start_ms = start_ms
            return [
                {
                    "incomeType": "REALIZED_PNL",
                    "income": "-2.1",
                    "time": opened_at_ms + 30_000,
                }
            ]

        async def get_position_history(
            self, symbol, *, start_ms, end_ms, page_index=0, page_size=100
        ):
            self.history_calls += 1
            assert symbol == "BTC-USDT"
            assert start_ms == opened_at_ms - 60_000
            return [
                {
                    "symbol": "BTC-USDT",
                    "positionSide": "LONG",
                    "openTime": opened_at_ms,
                    "updateTime": opened_at_ms + 30_000,
                    "avgClosePrice": "99.5",
                    "netProfit": "-2.2",
                    "positionCommission": "-0.1",
                    "totalFunding": "-0.1",
                    "positionId": "position-1",
                }
            ]

    delivered = []

    async def fake_deliver(item, *, pnl, message, send_tg, trades_path):
        delivered.append((dict(item), pnl, message))
        updated = dict(item, card_sent=True, followup_sent=True)
        return updated, True

    monkeypatch.setattr(income_report, "_deliver_pending_exit_followup", fake_deliver)
    client = Client()
    result = asyncio.run(
        resolve_pending_dogon(
            client,
            path=pending_path,
            trades_path=trades_path,
            now=1_700_000_100.0,
        )
    )

    assert result == 1
    assert client.history_calls == 1
    assert client.income_start_ms == opened_at_ms - 60_000
    assert load_pending(pending_path) == []
    assert len(delivered) == 1
    item, pnl, message = delivered[0]
    assert item["exit_price"] == 99.5
    assert pnl == -2.1
    assert "Position History" in message

    rows = [
        json.loads(line)
        for line in trades_path.read_text(encoding="utf-8").splitlines()
    ]
    row = next(item for item in rows if item.get("event") == "exit")
    assert row["pnl_source"] == "position_history"
    assert row["pnl"] == -2.1
    assert row["fees"] == -0.1
    assert row["funding_fee"] == -0.1
    assert row["exit"] == 99.5
    assert row["exit_price"] == 99.5
    assert row["position_id"] == "position-1"


def test_reused_signal_id_entry_events_are_selected_by_entry_context(tmp_path):
    from astra_bot.decision.live_income_report import (
        load_entry_events,
        select_entry_event,
    )

    path = tmp_path / "trades.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "event": "entry",
                    "symbol": "HYPE-USDT",
                    "side": "short",
                    "signal_id": "reused-signal",
                    "ts": 1_700_000_000.0,
                    "fill_price": "79.378",
                    "qty": "146.23651020",
                },
                {
                    "event": "entry",
                    "symbol": "HYPE-USDT",
                    "side": "short",
                    "signal_id": "reused-signal",
                    "ts": 1_700_001_140.0,
                    "fill_price": "79.925",
                    "qty": "80.78161181",
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    events = load_entry_events(path)[("HYPE-USDT", "reused-signal")]
    selected = select_entry_event(
        events,
        entry=79.925,
        qty=80.78161181,
    )
    assert selected is not None
    assert selected["open_ms"] == 1_700_001_140_000
    assert select_entry_event(events) is None


def test_patch_trade_exit_pnl_updates_only_matching_reused_signal_exit(tmp_path):
    from astra_bot.decision.live_income_report import patch_trade_exit_pnl

    path = tmp_path / "trades.jsonl"
    first = {
        "event": "exit",
        "symbol": "HYPE-USDT",
        "side": "short",
        "signal_id": "reused-signal",
        "entry": 79.378,
        "qty": 146.2365102,
        "pnl": None,
        "pnl_source": "pending_dogon",
    }
    second = {
        "event": "exit",
        "symbol": "HYPE-USDT",
        "side": "short",
        "signal_id": "reused-signal",
        "entry": 79.925,
        "qty": 80.78161181,
        "pnl": None,
        "pnl_source": "pending_dogon",
    }
    path.write_text(
        json.dumps(first) + "\n" + json.dumps(second) + "\n",
        encoding="utf-8",
    )

    assert patch_trade_exit_pnl(
        symbol="HYPE-USDT",
        signal_id="reused-signal",
        pnl=-11.9234,
        fees=-0.05,
        funding=0.0,
        pnl_source="position_history",
        path=path,
        entry=79.925,
        qty=80.78161181,
    )
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["pnl"] is None
    assert rows[0]["pnl_source"] == "pending_dogon"
    assert rows[1]["pnl"] == -11.9234
    assert rows[1]["pnl_source"] == "position_history"


def test_confirmed_exit_lookup_disambiguates_reused_signal_id(tmp_path):
    from astra_bot.decision.live_income_report import _find_confirmed_exit_row

    path = tmp_path / "trades.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "event": "exit",
                    "symbol": "HYPE-USDT",
                    "side": "short",
                    "signal_id": "reused-signal",
                    "entry": 79.378,
                    "qty": 146.2365102,
                    "pnl": -116.3273,
                    "pnl_source": "income",
                },
                {
                    "event": "exit",
                    "symbol": "HYPE-USDT",
                    "side": "short",
                    "signal_id": "reused-signal",
                    "entry": 79.925,
                    "qty": 80.78161181,
                    "pnl": -11.9234,
                    "pnl_source": "income",
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    row = _find_confirmed_exit_row(
        "HYPE-USDT",
        "reused-signal",
        path,
        entry=79.925,
        qty=80.78161181,
    )
    assert row is not None
    assert row["entry"] == 79.925
    assert row["pnl"] == -11.9234


def test_backfill_unqueued_exits_is_idempotent_and_repairs_stale_pending(tmp_path):
    from astra_bot.decision.live_income_report import (
        backfill_unqueued_exit_rows,
        load_pending,
        save_pending,
    )

    trades_path = tmp_path / "trades.jsonl"
    pending_path = tmp_path / "pending.json"
    flags_path = tmp_path / "tg_flags.json"
    flags_path.write_text(json.dumps({"flags": {}}), encoding="utf-8")

    btc_open_ms = 1_700_000_000_000
    eth_open_ms = 1_700_000_200_000
    rows = [
        {
            "event": "entry",
            "symbol": "BTC-USDT",
            "side": "long",
            "signal_id": "btc-orphan",
            "ts": btc_open_ms / 1000.0,
            "fill_price": 100.0,
            "qty": 1.0,
            "leverage": 3,
        },
        {
            "event": "exit",
            "symbol": "BTC-USDT",
            "side": "long",
            "signal_id": "btc-orphan",
            "ts": btc_open_ms / 1000.0 + 100,
            "entry": 100.0,
            "exit": None,
            "pnl": None,
            "exit_reason": "no_fills",
        },
        {
            "event": "entry",
            "symbol": "ETH-USDT",
            "side": "short",
            "signal_id": "eth-stale",
            "ts": eth_open_ms / 1000.0,
            "fill_price": 200.0,
            "qty": 2.0,
            "leverage": 3,
        },
        {
            "event": "exit",
            "symbol": "ETH-USDT",
            "side": "short",
            "signal_id": "eth-stale",
            "ts": eth_open_ms / 1000.0 + 100,
            "entry": 200.0,
            "exit": None,
            "pnl": 5.0,
            "pnl_source": "income_backfill",
        },
    ]
    trades_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    save_pending(
        [
            {
                "symbol": "ETH-USDT",
                "side": "short",
                "signal_id": "eth-stale",
                "since_ms": eth_open_ms + 3 * 60 * 60 * 1000,
                "entry": 200.0,
                "ts": eth_open_ms / 1000.0,
            }
        ],
        pending_path,
    )

    stats = backfill_unqueued_exit_rows(
        trades_path=trades_path,
        pending_path=pending_path,
        flags_path=flags_path,
        now=1_700_000_500.0,
    )
    assert stats == {"added": 1, "updated": 1, "ambiguous": 0, "expired": 0}
    items = load_pending(pending_path)
    assert len(items) == 2
    by_signal = {item["signal_id"]: item for item in items}
    assert by_signal["btc-orphan"]["since_ms"] == btc_open_ms - 60_000
    assert by_signal["btc-orphan"]["qty"] == 1.0
    assert by_signal["eth-stale"]["since_ms"] == eth_open_ms - 60_000
    assert by_signal["eth-stale"]["opened_at_ms"] == eth_open_ms
    assert by_signal["eth-stale"]["qty"] == 2.0
    assert by_signal["eth-stale"]["ts"] == 1_700_000_500.0

    second = backfill_unqueued_exit_rows(
        trades_path=trades_path,
        pending_path=pending_path,
        flags_path=flags_path,
        now=1_700_000_600.0,
    )
    assert second == {"added": 0, "updated": 0, "ambiguous": 0, "expired": 0}
    assert len(load_pending(pending_path)) == 2


@pytest.mark.asyncio
async def test_missing_exit_card_metadata_keeps_pending_item(tmp_path):
    from astra_bot.decision.live_income_report import _deliver_pending_exit_followup

    trades_path = tmp_path / "trades.jsonl"
    trades_path.write_text(
        json.dumps(
            {
                "event": "entry",
                "symbol": "BTC-USDT",
                "side": "long",
                "signal_id": "missing-exit-price",
                "ts": 1_700_000_000.0,
                "entry": 100.0,
                "qty": 1.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    sent = []

    async def send_tg(message):
        sent.append(message)

    item = {
        "symbol": "BTC-USDT",
        "side": "long",
        "signal_id": "missing-exit-price",
        "entry": 100.0,
        "qty": 1.0,
        "exit_price": None,
    }
    updated, delivered = await _deliver_pending_exit_followup(
        item,
        pnl=1.0,
        message="exit follow-up",
        send_tg=send_tg,
        trades_path=trades_path,
    )
    assert delivered is False
    assert updated["card_sent"] is False
    assert updated["card_skipped_missing_metadata"] is True
    assert updated["followup_sent"] is True
    assert sent == ["exit follow-up"]
