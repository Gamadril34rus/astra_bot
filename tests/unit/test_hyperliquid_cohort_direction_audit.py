from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.research import hyperliquid_cohort_direction_audit as audit_module


def test_public_fill_audit_separates_open_short_from_close_long(
    tmp_path: Path, monkeypatch,
) -> None:
    manifest = tmp_path / "wallet_manifest.json"
    manifest.write_text(
        json.dumps({
            "wallet_summaries": [
                {
                    "address": "0x" + "a" * 40,
                    "native_perp_fills": 5000,
                    "fills_per_active_day": 500,
                    "month_volume": 1000,
                    "top_markets_by_notional": [{"market": "BTC-PERP"}],
                },
                {
                    "address": "0x" + "b" * 40,
                    "native_perp_fills": 4000,
                    "fills_per_active_day": 400,
                    "month_volume": 900,
                    "top_markets_by_notional": [{"market": "ETH-PERP"}],
                },
            ]
        }),
        encoding="utf-8",
    )
    calls = []

    def fake_info(payload: dict) -> list[dict]:
        calls.append(payload)
        if payload["user"] == "0x" + "a" * 40:
            return [
                {
                    "coin": "BTC", "dir": "Open Short", "side": "A",
                    "crossed": True, "px": "100", "sz": "2", "oid": 1,
                    "fee": "0.1", "closedPnl": "0", "time": 1000,
                },
                {
                    "coin": "BTC", "dir": "Close Long", "side": "A",
                    "crossed": False, "px": "101", "sz": "1", "oid": 2,
                    "fee": "0.05", "closedPnl": "10", "time": 2000,
                },
            ]
        return [
            {
                "coin": "ETH", "dir": "Open Long", "side": "B",
                "crossed": True, "px": "2000", "sz": "1", "oid": 3,
                "fee": "0.2", "closedPnl": "0", "time": 3000,
            }
        ]

    monkeypatch.setattr(audit_module, "post_info", fake_info)
    monkeypatch.setattr(audit_module.time, "sleep", lambda _seconds: None)

    report = audit_module.audit(
        manifest,
        tmp_path / "out",
        lookback_hours=1,
        max_wallets=2,
        max_fills_per_day=5000,
    )

    btc = report["per_market"]["BTC-PERP"]
    assert btc["sell_open_short_notional_usd"] == 200.0
    assert btc["sell_close_long_notional_usd"] == 101.0
    assert abs(btc["sell_open_short_share_pct"] - (200 / 301 * 100)) < 1e-9
    assert btc["liquidation_fills"] == 0
    assert report["counts"]["wallets_queried"] == 2
    assert report["counts"]["wallets_with_fills"] == 2
    assert len(calls) == 2
    assert all(call["type"] == "userFillsByTime" for call in calls)

    csv_path = tmp_path / "out" / "hyperliquid_wallet_direction_audit.csv"
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 3
    assert "address" not in rows[0]
    summary = json.loads(
        (tmp_path / "out" / "hyperliquid_wallet_direction_summary.json").read_text()
    )
    assert summary["limitations"][2].startswith(
        "Hyperliquid Info API exposes at most 10,000"
    )
