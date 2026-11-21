from __future__ import annotations

import pandas as pd
import pytest
from scripts.research.replay_wallet_flow_multi_horizon import (
    BAR,
    closed_context,
    funding_for_short,
    non_overlapping_9h,
    replay,
)


def _bars(start: str = "2026-10-03T00:00:00Z", count: int = 24) -> pd.DataFrame:
    index = pd.date_range(start, periods=count, freq="30min")
    closes = [100.0] * count
    # At the 05:00 signal close, the last completed bar is index 9:
    # 1h return is positive while the 4h return remains negative.
    closes[1] = 99.0
    closes[7] = 95.0
    closes[9] = 96.5
    rows = []
    for i, close in enumerate(closes):
        rows.append({
            "open_time": int(index[i].timestamp() * 1000),
            "open_time_utc": index[i].isoformat(),
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1.0,
            "quote_volume": 0.0,
            "trades_count": 0,
        })
    return pd.DataFrame(rows)


def test_closed_context_uses_only_completed_bars():
    bars = _bars().assign(
        open_dt=lambda frame: pd.to_datetime(frame.open_time, unit="ms", utc=True)
    ).set_index("open_dt")
    context = closed_context(bars, pd.Timestamp("2026-10-03T05:00:00Z"))
    assert context is not None
    assert context[0] > 0
    assert context[1] < 0
    # The bar opening at 05:00 is not part of the context at its open.
    assert bars.index.searchsorted(
        pd.Timestamp("2026-10-03T05:00:00Z") - BAR, side="right"
    ) - 1 == 9


def test_funding_for_short_uses_exact_exit_settlement_and_horizon_coverage():
    funding = pd.DataFrame({
        "fund_dt": pd.to_datetime([
            "2026-10-03T04:00:00Z",
            "2026-10-03T08:00:00Z",
            "2026-10-03T12:00:00Z",
            "2026-10-03T16:00:00Z",
        ], utc=True),
        "rate": [0.0001, 0.0002, -0.0001, 0.0001],
    })
    entry = pd.Timestamp("2026-10-03T05:00:00Z")
    four_hour = funding_for_short(
        funding, entry, entry + pd.Timedelta(hours=4), 4.0, 240
    )
    assert four_hour == pytest.approx((0.02, True, 1))
    nine_hour = funding_for_short(
        funding, entry, entry + pd.Timedelta(hours=9), 4.0, 540
    )
    assert nine_hour[1] is True
    assert nine_hour[2] == 2
    assert nine_hour[0] == pytest.approx(0.01)


def test_non_overlapping_rule_keeps_entries_at_least_nine_hours_apart():
    base = pd.Timestamp("2026-10-03T00:00:00Z")
    rows = [
        {"entry_time": base + pd.Timedelta(hours=0), "id": 1},
        {"entry_time": base + pd.Timedelta(hours=8, minutes=59), "id": 2},
        {"entry_time": base + pd.Timedelta(hours=9), "id": 3},
    ]
    selected = non_overlapping_9h(rows)
    assert [row["id"] for row in selected] == [1, 3]


def test_replay_uses_same_candidate_entries_for_all_horizons(tmp_path):
    data_dir = tmp_path / "bingx"
    data_dir.mkdir()
    bars = _bars(count=40)
    funding_times = [
        "2026-10-02T20:00:00Z",
        "2026-10-03T00:00:00Z",
        "2026-10-03T04:00:00Z",
        "2026-10-03T08:00:00Z",
        "2026-10-03T12:00:00Z",
        "2026-10-03T16:00:00Z",
    ]
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT", "INJUSDT"):
        bars.to_csv(data_dir / f"{symbol}_30m.csv", index=False)
        pd.DataFrame({
            "funding_time": [
                int(pd.Timestamp(value).timestamp() * 1000)
                for value in funding_times
            ],
            "rate": [0.0001] * len(funding_times),
        }).to_csv(data_dir / f"funding_{symbol}.csv", index=False)

    events = pd.DataFrame([{
        "horizon": "9h",
        "metric": "flow_ratio",
        "market": "BTC-PERP",
        "bucket_ts": "2026-10-03 04:45:00+00",
        "signal_side": "sell_pressure",
        "signal_value": -0.25,
    }])
    events_path = tmp_path / "events.csv"
    events.to_csv(events_path, index=False)

    report = replay(
        events_path,
        data_dir,
        tmp_path / "out",
        bootstrap_resamples=100,
    )
    result = pd.read_csv(
        tmp_path / "out" / "wallet_flow_multi_horizon_events.csv"
    )
    candidate = result[
        (result.market == "BTC-PERP")
        & (result.sample_type == "candidate")
    ]
    assert sorted(candidate.horizon_minutes.tolist()) == [30, 60, 120, 240, 540]
    assert report["market_data"]["BTC-PERP"]["candidate_entries"] == 1
    assert report["equal_market_summary"]["540m"]["candidate_minus_baseline_pp"] is None
