"""Tests for public BSR clock calibration and summaries."""
from __future__ import annotations

import json

import pytest
from astra_bot.research.bsr.clock_sync import (
    build_clock_sample,
    parse_server_time,
    summarize_clock_sync,
)


def test_parse_bingx_server_time():
    assert parse_server_time({"code": 0, "data": {"serverTime": 1234}}, bingx=True) == 1234


def test_parse_binance_server_time():
    assert parse_server_time({"serverTime": 5678}, bingx=False) == 5678


@pytest.mark.parametrize("payload,bingx", [
    ({"code": 1, "data": {"serverTime": 1234}}, True),
    ({"serverTime": 0}, False),
    ({"serverTime": True}, False),
])
def test_rejects_invalid_server_time(payload, bingx):
    with pytest.raises(ValueError):
        parse_server_time(payload, bingx=bingx)


def test_clock_offset_uses_local_request_midpoint():
    row = build_clock_sample("bingx_swap", 1_010, 1_000, 1_004, 4.0)
    assert row["offset_ms"] == 8.0
    assert row["rtt_ms"] == 4.0


def test_clock_sample_rejects_invalid_order_and_venue():
    with pytest.raises(ValueError):
        build_clock_sample("unknown", 1_010, 1_000, 1_004, 4.0)
    with pytest.raises(ValueError):
        build_clock_sample("bingx_swap", 1_010, 1_005, 1_004, 4.0)


def test_clock_sync_summary_reports_offset_spread_and_drift(tmp_path):
    path = tmp_path / "clock.jsonl"
    rows = [
        {"kind": "clock_sync", "venue": "bingx_swap", "offset_ms": 10,
         "rtt_ms": 4, "local_receive_ms": 100},
        {"kind": "clock_sync", "venue": "bingx_swap", "offset_ms": 14,
         "rtt_ms": 8, "local_receive_ms": 200},
        {"kind": "clock_sync_error", "venue": "binance_spot"},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    report = summarize_clock_sync(path)
    bx = report["by_venue"]["bingx_swap"]
    assert bx["samples"] == 2
    assert bx["offset_ms"]["median"] == 12
    assert bx["offset_ms"]["spread"] == 4
    assert bx["offset_ms"]["first_to_last_drift"] == 4
    assert bx["rtt_ms"]["p50"] == 6
    assert report["by_venue"]["binance_spot"]["errors"] == 1


def test_clock_sync_summary_counts_invalid_utf8_without_aborting(tmp_path):
    path = tmp_path / "clock.jsonl"
    valid = {
        "kind": "clock_sync",
        "venue": "bingx_swap",
        "offset_ms": 10,
        "rtt_ms": 4,
        "local_receive_ms": 100,
    }
    path.write_bytes((json.dumps(valid) + "\\n").encode("utf-8") + b"\\xff\\n")
    report = summarize_clock_sync(path)
    assert report["malformed_records"] == 1
    assert report["by_venue"]["bingx_swap"]["samples"] == 1
