# ruff: noqa: I001
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from scripts.analyze_ironflow_wallet_confirmation import (
    HORIZONS,
    METRICS,
    analyze,
)


def test_horizons_and_metrics_are_explicit() -> None:
    assert HORIZONS == {"30m": 30, "1h": 60, "4h": 240, "9h": 540}
    assert METRICS == (
        "flow_ratio",
        "aggressive_flow_ratio",
        "wallet_consensus_ratio",
    )


def test_non_overlapping_samples_are_thinned_per_market_and_horizon(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 10, 3, 0, 15, tzinfo=timezone.utc)
    times = [start + timedelta(minutes=15 * i) for i in range(6)]
    cohort = pa.table({
        "market": ["BTC-PERP"] * 6,
        "bucket_ts": times,
        "cohort_signed_notional": [100.0] * 6,
        "cohort_participant_notional": [100.0] * 6,
        "cohort_aggressive_signed_notional": [80.0] * 6,
        "cohort_taker_notional": [80.0] * 6,
        "active_wallets": [5] * 6,
        "net_buy_wallets": [5] * 6,
        "net_sell_wallets": [0] * 6,
    })
    cohort_path = tmp_path / "cohort.parquet"
    pq.write_table(cohort, cohort_path)

    offsets = list(range(0, 616, 15))
    closes = [100.0 + i * 0.1 for i in range(len(offsets))]
    bars = pa.table({
        "market": ["BTC-PERP"] * len(offsets),
        "bucket_ts": [start + timedelta(minutes=i) for i in offsets],
        "close": closes,
    })
    bars_path = tmp_path / "bars.parquet"
    pq.write_table(bars, bars_path)

    report = analyze(cohort_path, bars_path, tmp_path / "out")
    assert report["counts"]["cohort_rows"] == 6
    assert report["counts"]["markets"] == 1
    rows = report["counts"]["study_rows"]
    assert rows > 0

    import csv

    with (tmp_path / "out" / "wallet_flow_confirmation_study.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        study = list(csv.DictReader(handle))

    def observations(horizon: str, sample_type: str) -> int:
        return sum(
            int(row["observations"])
            for row in study
            if row["horizon"] == horizon and row["metric"] == "flow_ratio"
            and row["sample_type"] == sample_type
        )

    assert observations("30m", "all") == 6
    assert observations("30m", "non_overlapping") == 3
    assert observations("1h", "non_overlapping") == 2
    assert observations("4h", "non_overlapping") == 1
    assert observations("9h", "non_overlapping") == 1
    assert all(float(row["mean_raw_return_pct"]) > 0 for row in study)
    assert all(float(row["baseline_return_pct"]) > 0 for row in study)
    assert all(
        int(row["negative_signal_observations"]) == 0 for row in study
    )
    expected_30m_baseline = sum(
        100.0 * (closes[i + 2] / closes[i] - 1.0)
        for i in range(len(closes) - 2)
    ) / (len(closes) - 2)
    assert all(
        abs(float(row["baseline_return_pct"]) - expected_30m_baseline) < 1e-9
        for row in study if row["horizon"] == "30m"
    )

    sides_path = tmp_path / "out" / "wallet_flow_confirmation_sides.csv"
    with sides_path.open(encoding="utf-8", newline="") as handle:
        sides = list(csv.DictReader(handle))
    assert sides
    events_path = tmp_path / "out" / "wallet_flow_confirmation_events.csv"
    with events_path.open(encoding="utf-8", newline="") as handle:
        events = list(csv.DictReader(handle))
    selected = [
        row for row in events
        if row["horizon"] == "30m" and row["metric"] == "flow_ratio"
        and row["market"] == "BTC-PERP"
    ]
    assert len(selected) == 3
    assert all("prior_1h_return_pct" in row for row in selected)
    assert all("prior_4h_return_pct" in row for row in selected)
    event_times = [
        datetime.fromisoformat(row["bucket_ts"]) for row in selected
    ]
    assert all(
        (event_times[i] - event_times[i - 1]).total_seconds() >= 1800
        for i in range(1, len(event_times))
    )
    assert all(
        abs(float(row["excess_directional_return_pct"]) -
            (float(row["directional_return_pct"]) -
             (1.0 if row["signal_side"] == "buy_pressure" else -1.0) *
             float(row["baseline_return_pct"]))) < 1e-9
        for row in selected
    )
    assert any(
        row["horizon"] == "30m"
        and row["metric"] == "flow_ratio"
        and row["sample_type"] == "non_overlapping"
        and row["signal_side"] == "buy_pressure"
        and int(row["observations"]) == 3
        and int(row["min_gap_minutes"]) >= 30
        and abs(float(row["baseline_return_pct"]) - expected_30m_baseline) < 1e-9
        and abs(float(row["excess_directional_return_pct"]) -
                (float(row["mean_directional_return_pct"]) -
                 float(row["baseline_return_pct"]))) < 1e-9
        for row in sides
    )

    summary = json.loads(
        (tmp_path / "out" / "wallet_flow_confirmation_summary.json").read_text()
    )
    assert summary["method"]["sample_types"] == ["all", "non_overlapping"]
