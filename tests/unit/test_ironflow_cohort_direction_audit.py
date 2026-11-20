import csv
import json
from pathlib import Path

from scripts.research import ironflow_cohort_direction_audit as audit_module


def test_direction_audit_aggregates_without_exporting_wallet_addresses(
    tmp_path: Path, monkeypatch,
) -> None:
    manifest = tmp_path / "wallet_manifest.json"
    manifest.write_text(
        json.dumps({
            "wallet_summaries": [
                {"address": "0x" + "a" * 40},
                {"address": "0x" + "b" * 40},
            ]
        }),
        encoding="utf-8",
    )
    calls = []

    def fake_query(sql: str) -> dict:
        calls.append(sql)
        market = next(
            name for name in audit_module.MARKETS if f"market = '{name}'" in sql
        )
        columns = [
            {"name": "market"}, {"name": "hour_utc"}, {"name": "side"},
            {"name": "direction"}, {"name": "is_taker"}, {"name": "fill_rows"},
            {"name": "order_ids"}, {"name": "wallets"}, {"name": "notional_usd"},
            {"name": "fees_usd"}, {"name": "realized_pnl_usd"},
            {"name": "liquidation_fills"}, {"name": "liquidation_notional_usd"},
        ]
        return {
            "columns": columns,
            "rows": [[
                market, "2026-11-20 09:00:00", "sell", "Open Short", True,
                10, 3, 2, 1250.0, 1.25, 25.0, 0, 0.0,
            ]],
            "query_seconds": 0.1,
        }

    monkeypatch.setattr(audit_module, "post_query", fake_query)
    monkeypatch.setattr(audit_module.time, "sleep", lambda _seconds: None)

    report = audit_module.audit(manifest, tmp_path / "out", lookback_hours=1)

    assert report["cohort_wallets"] == 2
    assert report["requests"] == 5
    assert report["aggregate_rows"] == 5
    assert len(calls) == 5
    for market in audit_module.MARKETS:
        key = "sell|Open Short|taker"
        row = report["directional_aggregates"][market][key]
        assert row["notional_usd"] == 1250.0
        assert row["realized_pnl_usd"] == 25.0

    csv_path = tmp_path / "out" / "ironflow_wallet_direction_hourly.csv"
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 5
    assert "address" not in rows[0]
    summary = json.loads(
        (tmp_path / "out" / "ironflow_wallet_direction_summary.json").read_text()
    )
    assert summary["history_tier"] == "keyless, trailing 24h"
