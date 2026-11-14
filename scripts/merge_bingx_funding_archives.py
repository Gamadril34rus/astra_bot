#!/usr/bin/env python3
"""Merge independently captured BingX USDT-M funding archives without imputing gaps."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path
from typing import Any

SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
DEFAULT_CORE = SYMBOLS
FIELDS = ("funding_time", "rate")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include a timezone: {value!r}")
    return int(parsed.astimezone(UTC).timestamp() * 1000)


def _iso_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()


def _load_manifest(directory: Path) -> dict[str, Any]:
    path = directory / "funding_manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"funding manifest is missing: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("exchange") != "BingX":
        raise ValueError(f"{path}: expected BingX source")
    if manifest.get("market") != "USDT-M perpetual swaps":
        raise ValueError(f"{path}: expected USDT-M perpetual swaps")
    if not isinstance(manifest.get("reports"), list):
        raise ValueError(f"{path}: unsupported funding manifest schema")
    return manifest


def _load_rows(directory: Path, manifest: dict[str, Any], symbol: str) -> dict[int, str]:
    report = next((row for row in manifest["reports"] if row.get("symbol") == symbol), None)
    if report is None:
        raise ValueError(f"{directory}: no manifest report for {symbol}")
    path = directory / f"funding_{symbol.replace('-', '')}.csv"
    if not path.is_file():
        raise FileNotFoundError(f"funding CSV is missing: {path}")
    expected_sha = report.get("sha256")
    actual_sha = _sha256(path)
    if expected_sha and expected_sha != actual_sha:
        raise ValueError(
            f"{path}: SHA-256 mismatch; manifest={expected_sha} actual={actual_sha}"
        )
    rows: dict[int, str] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if not set(FIELDS).issubset(row):
                raise ValueError(f"{path}: required fields are {FIELDS}")
            timestamp = int(row["funding_time"])
            try:
                rate = Decimal(row["rate"])
            except (InvalidOperation, TypeError) as exc:
                raise ValueError(f"{path}: invalid funding rate {row.get('rate')!r}") from exc
            if not rate.is_finite():
                raise ValueError(f"{path}: non-finite funding rate at {timestamp}")
            if timestamp in rows:
                raise ValueError(f"{path}: duplicate funding timestamp {timestamp}")
            rows[timestamp] = str(rate)
    return rows


def _latest_segment(rows: dict[int, str]) -> dict[str, Any]:
    times = sorted(rows)
    if len(times) < 3:
        raise ValueError("at least three funding events are required to verify cadence")
    gaps = [(right - left) / 3_600_000 for left, right in pairwise(times)]
    recent_gaps = gaps[-min(200, len(gaps)):]
    median_gap = float(statistics.median(recent_gaps))
    if median_gap <= 0:
        raise ValueError("funding cadence is not positive")
    threshold = max(1.5 * median_gap, median_gap + 1.0)
    breaks = [index for index, gap in enumerate(gaps) if gap > threshold]
    start_index = breaks[-1] + 1 if breaks else 0
    segment_times = times[start_index:]
    segment_gaps = [
        (right - left) / 3_600_000
        for left, right in pairwise(segment_times)
    ]
    return {
        "events_total": len(times),
        "events_latest_segment": len(segment_times),
        "median_gap_hours": round(median_gap, 4),
        "gap_threshold_hours": round(threshold, 4),
        "max_gap_hours_latest_segment": round(max(segment_gaps), 4) if segment_gaps else None,
        "latest_segment_start_ms": segment_times[0],
        "latest_segment_end_ms": segment_times[-1],
        "coverage_end_ms": int(segment_times[-1]),
        "break_count_before_latest_segment": len(breaks),
    }


def merge_archives(
    base_dir: Path,
    supplemental_dir: Path,
    out_dir: Path,
    *,
    symbols: tuple[str, ...] = SYMBOLS,
    core_symbols: tuple[str, ...] = DEFAULT_CORE,
    requested_start: str = "2025-11-13T00:00:00Z",
    requested_end: str = "2026-11-13T00:00:00Z",
    require_days: float = 365.0,
    base_run_id: str = "43213211531",
    base_artifact: str = "15m-pullback-continuation-research",
    supplemental_run_id: str = "current-run",
    supplemental_artifact: str = "bingx-usdm-funding-12m-raw",
) -> dict[str, Any]:
    base_manifest = _load_manifest(base_dir)
    supplemental_manifest = _load_manifest(supplemental_dir)
    start_ms, end_ms = _utc_ms(requested_start), _utc_ms(requested_end)
    if end_ms <= start_ms:
        raise ValueError("requested funding window must have positive duration")
    if not core_symbols or set(core_symbols) - set(symbols):
        raise ValueError("core symbols must be a non-empty subset of merged symbols")

    out_dir.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, Any]] = []
    coverage: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        base_rows = _load_rows(base_dir, base_manifest, symbol)
        supplemental_rows = _load_rows(supplemental_dir, supplemental_manifest, symbol)
        merged = dict(base_rows)
        for timestamp, rate in supplemental_rows.items():
            previous = merged.get(timestamp)
            if previous is not None and Decimal(previous) != Decimal(rate):
                raise ValueError(
                    f"{symbol}: conflicting BingX funding rate at {timestamp}: "
                    f"{previous} != {rate}"
                )
            merged[timestamp] = rate
        merged = dict(sorted(merged.items()))
        if not merged:
            raise ValueError(f"{symbol}: merged funding archive is empty")

        output_path = out_dir / f"funding_{symbol.replace('-', '')}.csv"
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(
                {"funding_time": timestamp, "rate": rate}
                for timestamp, rate in merged.items()
            )

        segment = _latest_segment(merged)
        segment_start = int(segment["latest_segment_start_ms"])
        segment_end = int(segment["latest_segment_end_ms"])
        first_gap_hours = max(0.0, (segment_start - start_ms) / 3_600_000)
        last_gap_hours = max(0.0, (end_ms - segment_end) / 3_600_000)
        edge_tolerance_hours = float(segment["gap_threshold_hours"])
        cadence_ok = (
            segment["max_gap_hours_latest_segment"] is None
            or float(segment["max_gap_hours_latest_segment"]) <= edge_tolerance_hours
        )
        # Funding is discrete (typically every 4h or 8h). The final settlement
        # before an exclusive window boundary still covers the interval when
        # it is within the symbol's verified cadence tolerance.
        leading_edge_ok = first_gap_hours <= edge_tolerance_hours
        trailing_edge_ok = last_gap_hours <= edge_tolerance_hours
        edge_ok = leading_edge_ok and trailing_edge_ok
        coverage_start = start_ms if leading_edge_ok else max(start_ms, segment_start)
        coverage_end = end_ms if trailing_edge_ok else min(end_ms, segment_end)
        symbol_days = max(0.0, (coverage_end - coverage_start) / 86_400_000)
        report = {
            "symbol": symbol,
            "file": str(output_path),
            "events": len(merged),
            "first_funding_time": next(iter(merged)),
            "last_funding_time": next(reversed(merged)),
            **segment,
            "coverage_start_utc": _iso_ms(coverage_start),
            "coverage_end_utc": _iso_ms(coverage_end),
            "coverage_days_in_requested_window": round(symbol_days, 4),
            "leading_uncovered_hours": round(first_gap_hours, 4),
            "trailing_uncovered_hours": round(last_gap_hours, 4),
            "cadence_ok": cadence_ok,
            "requested_window_edges_ok": edge_ok,
            "sha256": _sha256(output_path),
            "sources": [
                {"run_id": base_run_id, "artifact": base_artifact, "sha256": _sha256(base_dir / f"funding_{symbol.replace('-', '')}.csv")},
                {"run_id": supplemental_run_id, "artifact": supplemental_artifact, "sha256": _sha256(supplemental_dir / f"funding_{symbol.replace('-', '')}.csv")},
            ],
        }
        reports.append(report)
        coverage[symbol] = {
            "start_ms": coverage_start,
            "end_ms": coverage_end,
            "days": symbol_days,
            "cadence_ok": cadence_ok,
            "edges_ok": edge_ok,
        }

    common_start = max(coverage[symbol]["start_ms"] for symbol in core_symbols)
    common_end = min(coverage[symbol]["end_ms"] for symbol in core_symbols)
    common_days = max(0.0, (common_end - common_start) / 86_400_000)
    core_ok = all(
        coverage[symbol]["cadence_ok"] and coverage[symbol]["edges_ok"]
        for symbol in core_symbols
    )
    manifest = {
        "schema_version": 1,
        "exchange": "BingX",
        "market": "USDT-M perpetual swaps",
        "source_endpoint": "/openApi/swap/v2/quote/fundingRate",
        "requested_start_utc": _iso_ms(start_ms),
        "requested_end_utc": _iso_ms(end_ms),
        "promotion_core_symbols": list(core_symbols),
        "symbols": list(symbols),
        "source_artifacts": [
            {"run_id": base_run_id, "artifact": base_artifact},
            {"run_id": supplemental_run_id, "artifact": supplemental_artifact},
        ],
        "core_common_window": {
            "start_utc": _iso_ms(common_start),
            "end_utc": _iso_ms(common_end),
            "days": round(common_days, 4),
            "covers_required_days": common_days >= require_days,
            "all_symbols_cadence_and_edges_ok": core_ok,
        },
        "reports": reports,
        "policy": {
            "merge": "timestamp union; duplicate rates must match exactly",
            "gap_handling": "latest uninterrupted segment only; no funding imputation",
            "funding_intervals": "inferred from each symbol's recent median settlement cadence",
            "non_core_symbols": "reported separately and excluded from the promotion cohort",
        },
    }
    (out_dir / "funding_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not core_ok:
        raise RuntimeError(f"core funding coverage contains unresolved gaps: {core_symbols}")
    if common_days < require_days:
        raise RuntimeError(
            f"core common funding coverage is {common_days:.2f} days; "
            f"{require_days:.2f} required"
        )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--supplemental-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("data/bingx_funding_merged"))
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--core-symbols", default=",".join(DEFAULT_CORE))
    parser.add_argument("--requested-start", default="2025-11-13T00:00:00Z")
    parser.add_argument("--requested-end", default="2026-11-13T00:00:00Z")
    parser.add_argument("--require-days", type=float, default=365.0)
    parser.add_argument("--base-run-id", default="43213211531")
    parser.add_argument("--base-artifact", default="15m-pullback-continuation-research")
    parser.add_argument("--supplemental-run-id", default="current-run")
    parser.add_argument("--supplemental-artifact", default="bingx-usdm-funding-12m-raw")
    args = parser.parse_args()
    symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    core_symbols = tuple(item.strip().upper() for item in args.core_symbols.split(",") if item.strip())
    manifest = merge_archives(
        args.base_dir,
        args.supplemental_dir,
        args.out_dir,
        symbols=symbols,
        core_symbols=core_symbols,
        requested_start=args.requested_start,
        requested_end=args.requested_end,
        require_days=args.require_days,
        base_run_id=args.base_run_id,
        base_artifact=args.base_artifact,
        supplemental_run_id=args.supplemental_run_id,
        supplemental_artifact=args.supplemental_artifact,
    )
    print(json.dumps(manifest["core_common_window"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
