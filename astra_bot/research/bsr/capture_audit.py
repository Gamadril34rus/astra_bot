"""Read-only integrity audit for raw BSR JSONL captures.

The auditor never edits source files. It reports malformed rows, duplicate
trades, feed latency and depth sequence integrity so downstream event studies
can exclude bad windows with explicit reasons.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.isdecimal():
        parsed = int(value)
    else:
        return None
    return parsed if parsed > 0 else None


def audit_capture_files(
    paths: Iterable[str | Path],
    *,
    max_latency_ms: int = 5_000,
) -> dict[str, Any]:
    """Audit one or more captures without changing their bytes or contents."""
    if max_latency_ms < 0:
        raise ValueError("max_latency_ms cannot be negative")

    report: dict[str, Any] = {
        "schema_version": 1,
        "max_latency_ms": max_latency_ms,
        "files": [],
        "totals": {
            "lines": 0,
            "trade_records": 0,
            "bingx_swap_trade_records": 0,
            "binance_spot_trade_records": 0,
            "depth_timestamp_records": 0,
            "depth_invalid_timestamps": 0,
            "depth_negative_latency": 0,
            "depth_late_messages": 0,
            "parse_error_records": 0,
            "malformed_lines": 0,
            "duplicate_trades": 0,
            "invalid_timestamps": 0,
            "negative_latency": 0,
            "late_trades": 0,
            "depth_snapshots": 0,
            "depth_updates": 0,
            "depth_unsynchronized": 0,
            "depth_sequence_errors": 0,
            "connection_errors": 0,
        },
        "by_venue_symbol": {},
        "latency_ms": {"count": 0, "p50": None, "p95": None, "p99": None},
        "malformed_offsets": [],
        "duplicate_examples": [],
        "ready_for_downstream_filtering": False,
    }
    totals = report["totals"]
    latency_values: list[float] = []
    seen_trade_ids: set[tuple[str, str, str]] = set()
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "records": 0,
            "trades": 0,
            "depth_snapshots": 0,
            "depth_updates": 0,
            "depth_unsynchronized": 0,
            "depth_timestamp_invalid": 0,
            "depth_negative_latency": 0,
            "depth_late_messages": 0,
            "parse_error_records": 0,
            "duplicate_trades": 0,
            "invalid_timestamps": 0,
            "late_trades": 0,
            "connection_errors": 0,
            "latency_ms": [],
        }
    )
    depth_state: dict[tuple[str, str], int | None] = {}
    depth_latency_values: list[float] = []

    for raw_path in paths:
        path = Path(raw_path)
        file_report = {
            "path": str(path),
            "exists": path.is_file(),
            "size_bytes": path.stat().st_size if path.is_file() else 0,
            "lines": 0,
            "malformed_lines": 0,
            "kinds": {},
        }
        if not path.is_file():
            report["files"].append(file_report)
            continue

        kinds: Counter[str] = Counter()
        offset = 0
        with path.open("rb") as stream:
            for line_number, raw_line in enumerate(stream, start=1):
                current_offset = offset
                offset += len(raw_line)
                file_report["lines"] += 1
                totals["lines"] += 1
                try:
                    row = json.loads(raw_line)
                    if not isinstance(row, dict):
                        raise ValueError("JSONL row must be an object")
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                    file_report["malformed_lines"] += 1
                    totals["malformed_lines"] += 1
                    if len(report["malformed_offsets"]) < 100:
                        report["malformed_offsets"].append({
                            "path": str(path),
                            "line": line_number,
                            "byte_offset": current_offset,
                        })
                    continue

                venue = str(row.get("venue") or "unknown")
                kind = str(row.get("kind") or "unknown")
                raw_symbol = row.get("symbol")
                if not raw_symbol and kind == "market_event":
                    payload_for_symbol = row.get("payload")
                    if isinstance(payload_for_symbol, dict):
                        raw_symbol = payload_for_symbol.get("s")
                    if not raw_symbol and row.get("channel"):
                        raw_symbol = str(row["channel"]).split("@", 1)[0]
                symbol = str(raw_symbol or "unknown").upper()
                key = f"{venue}:{symbol}"
                stats = grouped[key]
                stats["records"] += 1
                kinds[kind] += 1
                if kind in {"parse_error", "unparsed"}:
                    totals["parse_error_records"] += 1
                    stats["parse_error_records"] += 1

                if kind == "trade":
                    totals["trade_records"] += 1
                    if venue == "bingx_swap":
                        totals["bingx_swap_trade_records"] += 1
                    elif venue == "binance_spot":
                        totals["binance_spot_trade_records"] += 1
                    stats["trades"] += 1
                    trade_id = str(row.get("trade_id") or "")
                    if trade_id:
                        identity = (venue, symbol, trade_id)
                        if identity in seen_trade_ids:
                            totals["duplicate_trades"] += 1
                            stats["duplicate_trades"] += 1
                            if len(report["duplicate_examples"]) < 100:
                                report["duplicate_examples"].append({
                                    "venue": venue,
                                    "symbol": symbol,
                                    "trade_id": trade_id,
                                    "path": str(path),
                                    "line": line_number,
                                })
                        else:
                            seen_trade_ids.add(identity)

                    exchange_ts = _positive_int(row.get("exchange_time_ms"))
                    receive_ts = _positive_int(row.get("received_at_ms"))
                    if exchange_ts is None or receive_ts is None:
                        totals["invalid_timestamps"] += 1
                        stats["invalid_timestamps"] += 1
                    else:
                        latency = receive_ts - exchange_ts
                        if latency < 0:
                            totals["negative_latency"] += 1
                        else:
                            latency_values.append(float(latency))
                            stats["latency_ms"].append(float(latency))
                            if latency > max_latency_ms:
                                totals["late_trades"] += 1
                                stats["late_trades"] += 1

                elif kind == "depth":
                    state_key = (venue, symbol)
                    payload = row.get("payload")
                    depth_exchange_ts = _positive_int(
                        payload.get("T", payload.get("time")) if isinstance(payload, dict) else None
                    )
                    depth_receive_ts = _positive_int(row.get("received_at_ms"))
                    if depth_exchange_ts is None or depth_receive_ts is None:
                        totals["depth_invalid_timestamps"] += 1
                        stats["depth_timestamp_invalid"] += 1
                    else:
                        totals["depth_timestamp_records"] += 1
                        depth_latency = depth_receive_ts - depth_exchange_ts
                        if depth_latency < 0:
                            totals["depth_negative_latency"] += 1
                            stats["depth_negative_latency"] += 1
                        else:
                            depth_latency_values.append(float(depth_latency))
                            if depth_latency > max_latency_ms:
                                totals["depth_late_messages"] += 1
                                stats["depth_late_messages"] += 1
                    payload = row.get("payload")
                    action = payload.get("action") if isinstance(payload, dict) else None
                    update_id = _positive_int(
                        payload.get("lastUpdateId") if isinstance(payload, dict) else None
                    )
                    synchronized = row.get("synchronized") is True
                    if action == "all":
                        if synchronized and update_id is not None:
                            depth_state[state_key] = update_id
                            totals["depth_snapshots"] += 1
                            stats["depth_snapshots"] += 1
                        else:
                            depth_state[state_key] = None
                            totals["depth_unsynchronized"] += 1
                            stats["depth_unsynchronized"] += 1
                    elif action == "update":
                        totals["depth_updates"] += 1
                        stats["depth_updates"] += 1
                        previous = depth_state.get(state_key)
                        if update_id is not None and previous is not None and update_id != previous + 1:
                            depth_state[state_key] = None
                            totals["depth_sequence_errors"] += 1
                            totals["depth_unsynchronized"] += 1
                            stats["depth_unsynchronized"] += 1
                        elif (not synchronized or update_id is None or previous is None):
                            depth_state[state_key] = None
                            totals["depth_unsynchronized"] += 1
                            stats["depth_unsynchronized"] += 1
                        else:
                            depth_state[state_key] = update_id
                    else:
                        depth_state[state_key] = None
                        totals["depth_unsynchronized"] += 1
                        stats["depth_unsynchronized"] += 1

                elif kind == "connection_error":
                    totals["connection_errors"] += 1
                    stats["connection_errors"] += 1
                    for state_key in list(depth_state):
                        if state_key[0] == venue:
                            depth_state[state_key] = None

        file_report["kinds"] = dict(sorted(kinds.items()))
        report["files"].append(file_report)

    report["by_venue_symbol"] = {
        key: {
            **{name: value for name, value in stats.items() if name != "latency_ms"},
            "latency_ms": {
                "count": len(stats["latency_ms"]),
                "p50": _percentile(stats["latency_ms"], 0.50),
                "p95": _percentile(stats["latency_ms"], 0.95),
                "p99": _percentile(stats["latency_ms"], 0.99),
            },
        }
        for key, stats in sorted(grouped.items())
    }
    report["depth_message_age_ms"] = {
        "count": len(depth_latency_values),
        "p50": _percentile(depth_latency_values, 0.50),
        "p95": _percentile(depth_latency_values, 0.95),
        "p99": _percentile(depth_latency_values, 0.99),
    }
    report["latency_ms"] = {
        "count": len(latency_values),
        "p50": _percentile(latency_values, 0.50),
        "p95": _percentile(latency_values, 0.95),
        "p99": _percentile(latency_values, 0.99),
    }
    report["ready_for_downstream_filtering"] = (
        totals["lines"] > 0
        and all(item["exists"] for item in report["files"])
        and totals["bingx_swap_trade_records"] > 0
        and totals["binance_spot_trade_records"] > 0
        and totals["malformed_lines"] == 0
        and totals["depth_snapshots"] > 0
        and totals["trade_records"] > 0
        and totals["parse_error_records"] == 0
        and totals["depth_timestamp_records"] > 0
        and totals["depth_invalid_timestamps"] == 0
        and totals["depth_negative_latency"] == 0
        and totals["depth_late_messages"] == 0
        and totals["depth_unsynchronized"] == 0
        and totals["duplicate_trades"] == 0
        and totals["invalid_timestamps"] == 0
        and totals["late_trades"] == 0
        and totals["negative_latency"] == 0
        and totals["depth_sequence_errors"] == 0
    )
    return report
