from __future__ import annotations

import pandas as pd

from scripts.analyze_bingx_positioning_shadow import (
    build_symbol_shadow,
    summarize_shadow,
)


def _candles(count: int = 12) -> pd.DataFrame:
    rows = []
    interval = 30 * 60_000
    for index in range(count):
        close = 100.0 + index
        rows.append({
            "open_time": index * interval,
            "decision_time": (index + 1) * interval,
            "open": close - 0.5,
            "high": close + 0.5,
            "low": close - 1.0,
            "close": close,
            "volume": 100.0,
            "quote_volume": 10_000.0,
            "taker_buy_volume": 60.0,
            "taker_buy_quote_volume": 6_000.0,
        })
    return pd.DataFrame(rows)


def _oi(times: list[int], values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "symbol": ["BTC-USDT"] * len(times),
        "source_time": times,
        "observed_at_ms": times,
        "open_interest_base": values,
    })


def test_oi_join_uses_only_the_last_closed_candle() -> None:
    candles = _candles()
    # Observation at 2h + 2m must use the candle closed at 2h, never the one
    # that is still forming after 2h.
    observations = _oi(
        [2 * 60 * 60_000 + 2 * 60_000, 2 * 60 * 60_000 + 32 * 60_000],
        [100.0, 101.0],
    )

    frame = build_symbol_shadow(candles, observations)

    assert not frame.empty
    assert frame.iloc[0]["candle_close_time"] == 2 * 60 * 60_000
    assert frame.iloc[0]["snapshot_age_minutes"] == 2.0
    assert frame.iloc[0]["taker_buy_share_pct"] == 60.0
    assert frame.iloc[0]["taker_imbalance_pct"] == 20.0
    assert frame.iloc[0]["positioning_state"] == "price_up_oi_up_taker_buy"


def test_oi_change_uses_local_observation_sequence_not_exchange_time() -> None:
    interval = 30 * 60_000
    candles = _candles()
    observations = _oi(
        [2 * interval + 60_000, 3 * interval + 60_000],
        [100.0, 102.0],
    )
    observations["source_time"] = [1_000, 1_000]

    frame = build_symbol_shadow(candles, observations)

    assert len(frame) == 1
    assert frame.iloc[0]["oi_change_pct"] == 2.0
    assert frame.iloc[0]["oi_observation_interval_minutes"] == 30.0


def test_summary_labels_are_descriptive_only() -> None:
    frame = pd.DataFrame({
        "symbol": ["BTC-USDT"],
        "observed_at_utc": ["2026-11-10T00:00:00+00:00"],
        "positioning_state": ["price_up_oi_up_taker_buy"],
        "forward_30m_pct": [0.2],
        "forward_60m_pct": [0.3],
        "forward_120m_pct": [0.4],
        "forward_240m_pct": [0.5],
        "forward_540m_pct": [0.6],
    })

    summary = summarize_shadow(frame)

    assert summary["snapshots"] == 1
    assert summary["states"]["price_up_oi_up_taker_buy"]["n_30m"] == 1
    assert "do not reveal participant identity" in summary["warning"]
