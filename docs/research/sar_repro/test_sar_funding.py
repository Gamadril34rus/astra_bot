"""Tests for historical funding collection and trade accounting."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sar_funding import apply_funding_to_trades, fetch_funding_rates


def _trade(side="long", entry=1000, exit_=2000):
    return {
        "symbol": "BTC-USDT",
        "side": side,
        "entry_ts": entry,
        "exit_ts": exit_,
        "entry_fill": 100.0,
        "net_pnl_pct": 0.02,
    }


def test_positive_funding_is_paid_by_long_and_received_by_short():
    event = {"fundingTime": 1500, "fundingRate": 0.001, "markPrice": 110.0}
    long = apply_funding_to_trades([_trade("long")], {"BTC-USDT": [event]})[0]
    short = apply_funding_to_trades([_trade("short")], {"BTC-USDT": [event]})[0]
    assert long["funding_pnl_pct"] == -0.0011
    assert short["funding_pnl_pct"] == 0.0011
    assert long["net_pnl_pct"] == 0.0189
    assert short["net_pnl_pct"] == 0.0211


def test_settlements_at_entry_and_exit_are_excluded():
    events = [
        {"fundingTime": 1000, "fundingRate": 0.001, "markPrice": 100.0},
        {"fundingTime": 2000, "fundingRate": 0.001, "markPrice": 100.0},
    ]
    result = apply_funding_to_trades([_trade()], {"BTC-USDT": events})[0]
    assert result["funding_events"] == 0
    assert result["funding_pnl_pct"] == 0.0


def test_intrabar_stop_uses_conservative_bar_open_funding_cutoff():
    trade = _trade(entry=1000, exit_=2500)
    trade["funding_cutoff_ts"] = 1500
    events = [
        {"fundingTime": 1200, "fundingRate": 0.001, "markPrice": 100.0},
        {"fundingTime": 1500, "fundingRate": 0.001, "markPrice": 100.0},
        {"fundingTime": 1800, "fundingRate": 0.001, "markPrice": 100.0},
    ]
    result = apply_funding_to_trades([trade], {"BTC-USDT": events})[0]
    assert result["funding_events"] == 1
    assert result["funding_pnl_pct"] == -0.001


def test_funding_fetch_pages_back_and_deduplicates():
    class Response:
        status_code = 200

        def __init__(self, rows):
            self.rows = rows

        def json(self):
            return {"code": 0, "data": self.rows}

    batches = [
        [
            {"fundingTime": 3000, "fundingRate": "0.1", "markPrice": "100"},
            {"fundingTime": 2000, "fundingRate": "0.2", "markPrice": "101"},
        ],
        [
            {"fundingTime": 2000, "fundingRate": "0.2", "markPrice": "101"},
            {"fundingTime": 1000, "fundingRate": "0.3", "markPrice": "102"},
        ],
    ]
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params)
        return Response(batches.pop(0))

    rows = fetch_funding_rates("BTC-USDT", 1000, 3000, request_get=fake_get, sleep=lambda _: None)
    assert [row["fundingTime"] for row in rows] == [1000, 2000, 3000]
    assert len(calls) == 2


def test_funding_rejects_invalid_range():
    try:
        fetch_funding_rates("BTC-USDT", 2, 1)
    except ValueError:
        return
    raise AssertionError("invalid range was accepted")
