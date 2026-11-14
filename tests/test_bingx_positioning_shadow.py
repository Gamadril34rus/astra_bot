from __future__ import annotations

import pandas as pd
import pytest
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
        [
            60 * 60_000 + 2 * 60_000,
            2 * 60 * 60_000 + 2 * 60_000,
            2 * 60 * 60_000 + 32 * 60_000,
        ],
        [99.0, 100.0, 101.0],
    )

    frame = build_symbol_shadow(candles, observations)

    assert len(frame) == 2
    assert frame.iloc[0]["candle_close_time"] == 2 * 60 * 60_000
    assert frame.iloc[0]["snapshot_age_minutes"] == 2.0
    assert frame.iloc[0]["taker_buy_share_pct"] == 60.0
    assert frame.iloc[0]["taker_imbalance_pct"] == pytest.approx(20.0)
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
    assert frame.iloc[0]["oi_change_pct"] == pytest.approx(2.0)
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


def test_missing_taker_flow_keeps_valid_oi_price_observations() -> None:
    candles = _candles()
    candles["quote_volume"] = 0.0
    candles["taker_buy_volume"] = float("nan")
    candles["taker_buy_quote_volume"] = float("nan")
    observations = _oi(
        [60 * 60_000 + 2 * 60_000, 2 * 60 * 60_000 + 2 * 60_000],
        [99.0, 100.0],
    )

    frame = build_symbol_shadow(candles, observations)

    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["positioning_state"] == "price_up_oi_up_taker_unknown"
    assert pd.isna(row["taker_buy_share_pct"])
    assert pd.isna(row["taker_imbalance_pct"])

    summary = summarize_shadow(frame)
    assert summary["snapshots"] == 1
    assert summary["taker_flow_available_snapshots"] == 0
    assert summary["taker_flow_coverage_pct"] == 0.0


def test_candle_files_without_optional_taker_columns_are_accepted(tmp_path) -> None:
    from scripts.analyze_bingx_positioning_shadow import load_candles

    path = tmp_path / "BTCUSDT_30m.csv"
    _candles().drop(
        columns=["quote_volume", "taker_buy_volume", "taker_buy_quote_volume"]
    ).to_csv(path, index=False)

    frame = load_candles(path)

    assert len(frame) == 12
    assert frame["taker_buy_volume"].isna().all()
    assert frame["taker_buy_quote_volume"].isna().all()
