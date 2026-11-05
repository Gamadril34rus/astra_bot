from __future__ import annotations

import json
from pathlib import Path

from scripts.zeus_analytics import build_report, generate


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def paper_row(
    trade_id: str,
    *,
    pnl: float,
    r_multiple: float,
    opened_at: int,
    strategy: str = "zeus_channel_boundary_4h",
) -> dict:
    return {
        "id": trade_id,
        "symbol": "BTC-USDT",
        "direction": "long",
        "pnl": pnl,
        "fees": 0.1,
        "r_multiple": r_multiple,
        "strategy": strategy,
        "opened_at": opened_at,
        "closed_at": opened_at + 60_000,
    }


def test_paper_partial_legs_are_one_trade_and_live_env_is_not_guessed(
    tmp_path: Path,
) -> None:
    write_jsonl(
        tmp_path / "models" / "zeus_paper_trades.jsonl",
        [
            paper_row("p1", pnl=1.0, r_multiple=0.4, opened_at=1_790_000_000_000),
            paper_row("p1", pnl=2.0, r_multiple=0.6, opened_at=1_790_000_000_000),
            paper_row(
                "p2",
                pnl=-1.0,
                r_multiple=-1.0,
                opened_at=1_790_100_000_000,
                strategy="zeus_wedge_retest_4h",
            ),
        ],
    )
    write_jsonl(
        tmp_path / "models" / "zeus_live_trades.jsonl",
        [
            {"event": "exit", "pnl": 4.0, "ts": 1_790_200_000},
            {
                "event": "exit",
                "pnl": 5.0,
                "ts": 1_790_200_100,
                "env": "vst",
                "strategy": "zeus_channel_boundary",
            },
        ],
    )

    report = build_report(tmp_path)

    assert "| paper | channel | ∞ | 100.00 | 1.000 | 3.0000 | 0.2000 |" in report
    assert "| paper | wedge | 0.00 | 0.00 | -1.000 | -1.0000 | 0.1000 |" in report
    assert "VST | channel" in report
    assert "live / UNKNOWN" in report
    assert "UNKNOWN не считается VST" in report


def test_zone_funnel_health_and_archive_empty_sections(tmp_path: Path) -> None:
    write_jsonl(
        tmp_path / "models" / "zeus_live_journal.jsonl",
        [
            {
                "event": "zone_shadow",
                "symbol": "BTC-USDT",
                "ts": 1_790_000_000,
                "in_zone": True,
                "nearest_above": {"distance_pct": 0.4},
                "zones": [{"center": 100, "min_distance_pct": 0.2}],
            },
            {
                "event": "zone_shadow_error",
                "symbol": "ETH-USDT",
                "ts": 1_790_000_030,
                "error": "TimeoutError",
            },
            {
                "event": "pending_signal",
                "symbol": "BTC-USDT",
                "side": "long",
                "ts": 1_790_000_040,
            },
            {
                "event": "arm_summary",
                "candidates": 1,
                "entered": ["BTC-USDT|long"],
                "skipped": {"conf": 2},
                "ts": 1_790_000_050,
            },
            {"event": "arm_skip", "reason": "conf", "ts": 1_790_000_060},
        ],
    )
    write_jsonl(
        tmp_path / "models" / "zeus_live_trades.jsonl",
        [
            {"event": "entry", "symbol": "BTC-USDT", "side": "long", "ts": 1_790_000_070},
            {
                "event": "exit",
                "symbol": "BTC-USDT",
                "side": "long",
                "pnl": 2.0,
                "ts": 1_790_100_000,
            },
        ],
    )
    write_jsonl(
        tmp_path / "models" / "zeus_live_entries.jsonl",
        [
            {
                "event": "entry_failed",
                "symbol": "ETH-USDT",
                "ts": 1_790_000_080,
                "error_msg": "100410",
            }
        ],
    )

    report = build_report(tmp_path)

    assert "approach observations | 1" in report
    assert "in-zone observations | 1" in report
    assert "minimum observed distance, %" in report
    assert "pending_signal candidates | 1" in report
    assert "arm_summary candidate count | 1" in report
    assert "conf | 2" in report
    assert "100410 occurrences in readable structured rows | 1" in report
    assert "COPY_ZEUS archive ticks | нет данных за период" in report


def test_empty_sources_are_explicit_and_generation_is_deterministic(
    tmp_path: Path,
) -> None:
    (tmp_path / "models").mkdir()
    first = generate(tmp_path)
    first_text = first.read_text(encoding="utf-8")
    second = generate(tmp_path)

    assert first == second
    assert first_text == second.read_text(encoding="utf-8")
    assert "нет данных за период" in first_text
    assert (tmp_path / "reports" / "zeus_analytics" / "summary.md").is_file()
    assert not (tmp_path / "models" / "zeus_live_journal.jsonl").exists()
    assert list((tmp_path / "models").iterdir()) == []

def test_report_json_rows_ignores_dashboard_output_directory(tmp_path: Path) -> None:
    from scripts.zeus_analytics import report_json_rows

    dashboard = tmp_path / "reports" / "astra_dashboard" / "dashboard.json"
    dashboard.parent.mkdir(parents=True)
    dashboard.write_text(
        json.dumps({"event": "copy_zeus_archive", "ts": 1_790_000_000}),
        encoding="utf-8",
    )
    rows = report_json_rows(
        tmp_path / "reports",
        tmp_path / "reports" / "zeus_analytics",
    )
    assert rows == []
