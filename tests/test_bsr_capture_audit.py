"""Tests for immutable BSR capture integrity auditing."""
from __future__ import annotations

import json

from astra_bot.research.bsr.capture_audit import audit_capture_files


def _write_jsonl(path, rows):
    with path.open("wb") as stream:
        for row in rows:
            stream.write(json.dumps(row).encode("utf-8") + b"\n")


def test_audit_reports_latency_percentiles_and_clean_capture(tmp_path):
    path = tmp_path / "clean.jsonl"
    _write_jsonl(path, [
        {
            "venue": "binance_spot",
            "kind": "trade",
            "symbol": "BTCUSDT",
            "trade_id": "1",
            "exchange_time_ms": 1_000,
            "received_at_ms": 1_120,
        },
        {
            "venue": "bingx_swap",
            "kind": "trade",
            "symbol": "BTC-USDT",
            "trade_id": "bingx-1",
            "exchange_time_ms": 1_000,
            "received_at_ms": 1_130,
        },
        {
            "venue": "bingx_swap",
            "kind": "depth",
            "symbol": "BTC-USDT",
            "synchronized": True,
            "received_at_ms": 1_150,
            "payload": {
                "action": "all",
                "lastUpdateId": 10,
                "time": 1_000,
                "bids": [["100", "2"]],
                "asks": [["101", "3"]],
            },
        },
    ])

    report = audit_capture_files([path])

    assert report["totals"]["lines"] == 3
    assert report["latency_ms"] == {
        "count": 2,
        "p50": 120.0,
        "p95": 130.0,
        "p99": 130.0,
    }
    assert report["totals"]["bingx_swap_trade_records"] == 1
    assert report["totals"]["binance_spot_trade_records"] == 1
    assert report["totals"]["depth_snapshots"] == 1
    assert report["ready_for_downstream_filtering"] is True


def test_audit_finds_malformed_line_and_preserves_byte_offset(tmp_path):
    path = tmp_path / "broken.jsonl"
    first = b'{"venue":"bingx_swap","kind":"heartbeat"}\n'
    path.write_bytes(first + b"{not-json}\n")

    report = audit_capture_files([path])

    assert report["totals"]["malformed_lines"] == 1
    assert report["malformed_offsets"] == [{
        "path": str(path),
        "line": 2,
        "byte_offset": len(first),
    }]
    assert report["ready_for_downstream_filtering"] is False


def test_audit_detects_duplicate_trade_ids_across_files(tmp_path):
    first = tmp_path / "bingx.jsonl"
    second = tmp_path / "binance.jsonl"
    row = {
        "venue": "bingx_swap",
        "kind": "trade",
        "symbol": "BTC-USDT",
        "trade_id": "same-id",
        "exchange_time_ms": 1_000,
        "received_at_ms": 1_100,
    }
    _write_jsonl(first, [row])
    _write_jsonl(second, [row])

    report = audit_capture_files([first, second])

    assert report["totals"]["duplicate_trades"] == 1
    assert report["duplicate_examples"][0]["trade_id"] == "same-id"


def test_audit_detects_depth_sequence_gap(tmp_path):
    path = tmp_path / "depth.jsonl"
    base = {
        "venue": "bingx_swap",
        "kind": "depth",
        "symbol": "ETH-USDT",
        "synchronized": True,
    }
    _write_jsonl(path, [
        {
            **base,
            "payload": {"action": "all", "lastUpdateId": 40,
                        "bids": [["100", "1"]], "asks": [["101", "1"]]},
        },
        {**base, "payload": {"action": "update", "lastUpdateId": 41}},
        {**base, "payload": {"action": "update", "lastUpdateId": 43}},
        {**base, "payload": {"action": "update", "lastUpdateId": 44}},
    ])

    report = audit_capture_files([path])

    assert report["totals"]["depth_snapshots"] == 1
    assert report["totals"]["depth_updates"] == 3
    assert report["totals"]["depth_sequence_errors"] == 1
    assert report["totals"]["depth_unsynchronized"] == 1


def test_audit_counts_negative_and_late_trade_latency(tmp_path):
    path = tmp_path / "latency.jsonl"
    _write_jsonl(path, [
        {
            "venue": "binance_spot",
            "kind": "trade",
            "symbol": "ETHUSDT",
            "trade_id": "negative",
            "exchange_time_ms": 2_000,
            "received_at_ms": 1_999,
        },
        {
            "venue": "binance_spot",
            "kind": "trade",
            "symbol": "ETHUSDT",
            "trade_id": "late",
            "exchange_time_ms": 1_000,
            "received_at_ms": 7_000,
        },
    ])

    report = audit_capture_files([path], max_latency_ms=5_000)

    assert report["totals"]["negative_latency"] == 1
    assert report["totals"]["late_trades"] == 1
    assert report["latency_ms"]["p50"] == 6_000.0


def test_audit_missing_file_is_reported_not_treated_as_empty_success(tmp_path):
    report = audit_capture_files([tmp_path / "missing.jsonl"])

    assert report["files"][0]["exists"] is False
    assert report["ready_for_downstream_filtering"] is False



def test_audit_reports_depth_message_age_separately(tmp_path):
    path = tmp_path / "depth-age.jsonl"
    _write_jsonl(path, [
        {
            "venue": "bingx_swap",
            "kind": "depth",
            "symbol": "BTC-USDT",
            "received_at_ms": 1_350,
            "synchronized": True,
            "payload": {
                "action": "all",
                "lastUpdateId": 1,
                "time": 1_100,
                "bids": [["100", "1"]],
                "asks": [["101", "1"]],
            },
        },
        {
            "venue": "bingx_swap",
            "kind": "trade",
            "symbol": "BTC-USDT",
            "trade_id": "1",
            "exchange_time_ms": 1_000,
            "received_at_ms": 1_100,
        },
    ])

    report = audit_capture_files([path], max_latency_ms=500)

    assert report["depth_message_age_ms"] == {
        "count": 1,
        "p50": 250.0,
        "p95": 250.0,
        "p99": 250.0,
    }
    assert report["latency_ms"]["p50"] == 100.0


def test_audit_readiness_fails_on_depth_timestamp_or_parse_errors(tmp_path):
    path = tmp_path / "bad-depth.jsonl"
    _write_jsonl(path, [
        {
            "venue": "bingx_swap",
            "kind": "depth",
            "symbol": "BTC-USDT",
            "received_at_ms": 1_200,
            "synchronized": True,
            "payload": {
                "action": "all",
                "lastUpdateId": 1,
                "bids": [["100", "1"]],
                "asks": [["101", "1"]],
            },
        },
        {
            "venue": "binance_spot",
            "kind": "parse_error",
            "received_at_ms": 1_200,
        },
    ])

    report = audit_capture_files([path])

    assert report["totals"]["depth_invalid_timestamps"] == 1
    assert report["totals"]["parse_error_records"] == 1
    assert report["ready_for_downstream_filtering"] is False



def test_audit_readiness_requires_both_venue_trade_feeds_and_all_files(tmp_path):
    path = tmp_path / "binance-only.jsonl"
    _write_jsonl(path, [
        {
            "venue": "binance_spot",
            "kind": "trade",
            "symbol": "BTCUSDT",
            "trade_id": "1",
            "exchange_time_ms": 1_000,
            "received_at_ms": 1_100,
        },
        {
            "venue": "bingx_swap",
            "kind": "depth",
            "symbol": "BTC-USDT",
            "synchronized": True,
            "received_at_ms": 1_100,
            "payload": {
                "action": "all",
                "lastUpdateId": 1,
                "T": 1_000,
                "bids": [["100", "1"]],
                "asks": [["101", "1"]],
            },
        },
    ])

    report = audit_capture_files([path, tmp_path / "missing-binance.jsonl"])

    assert report["totals"]["binance_spot_trade_records"] == 1
    assert report["totals"]["bingx_swap_trade_records"] == 0
    assert report["ready_for_downstream_filtering"] is False


def test_audit_rejects_fractional_timestamp_values(tmp_path):
    path = tmp_path / "fractional-timestamp.jsonl"
    _write_jsonl(path, [
        {
            "venue": "binance_spot",
            "kind": "trade",
            "symbol": "BTCUSDT",
            "trade_id": "1",
            "exchange_time_ms": 1000.5,
            "received_at_ms": 1100,
        },
    ])

    report = audit_capture_files([path])

    assert report["totals"]["invalid_timestamps"] == 1



def test_audit_recovers_depth_timestamp_from_bingx_time_field(tmp_path):
    path = tmp_path / "bingx-time-field.jsonl"
    _write_jsonl(path, [
        {
            "venue": "bingx_swap", "kind": "depth", "symbol": "BTC-USDT",
            "synchronized": True, "received_at_ms": 1_250,
            "payload": {
                "action": "all", "lastUpdateId": 10, "time": 1_000,
                "bids": [["100", "1"]], "asks": [["101", "1"]],
            },
        },
    ])
    report = audit_capture_files([path])
    assert report["totals"]["depth_timestamp_records"] == 1
    assert report["totals"]["depth_invalid_timestamps"] == 0
    assert report["depth_message_age_ms"]["p50"] == 250.0


def test_audit_counts_connection_errors_and_depth_sequence_gap(tmp_path):
    path = tmp_path / "resync.jsonl"
    _write_jsonl(path, [
        {
            "venue": "bingx_swap", "kind": "depth", "symbol": "BTC-USDT",
            "synchronized": True, "received_at_ms": 1_100,
            "payload": {"action": "all", "lastUpdateId": 10, "time": 1_000},
        },
        {
            "venue": "bingx_swap", "kind": "depth", "symbol": "BTC-USDT",
            "synchronized": False, "received_at_ms": 1_200,
            "payload": {"action": "update", "lastUpdateId": 12, "time": 1_100},
        },
        {
            "venue": "bingx_swap", "kind": "connection_error",
            "symbol": "BTC-USDT", "received_at_ms": 1_200,
            "reason": "depth_book_invalidated",
        },
        {
            "venue": "bingx_swap", "kind": "depth", "symbol": "BTC-USDT",
            "synchronized": True, "received_at_ms": 1_400,
            "payload": {"action": "all", "lastUpdateId": 20, "time": 1_300},
        },
    ])
    report = audit_capture_files([path])
    assert report["totals"]["connection_errors"] == 1
    assert report["totals"]["depth_sequence_errors"] == 1
    assert report["totals"]["depth_snapshots"] == 2
