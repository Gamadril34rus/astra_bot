#!/usr/bin/env python3
"""Read-only OKX USDT swap historical archive probe for research data coverage."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import itertools
import json
import time
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

BASE_URL = "https://www.okx.com"
ARCHIVE_PATH = "/api/v5/public/market-data-history"
SYMBOLS = ("BTC", "ETH", "SOL", "HYPE", "INJ")
HK_TZ = timezone(timedelta(hours=8))
USER_AGENT = "ASTRA-BOT-read-only-OKX-research/1.0"


def _request_json(path: str, params: dict[str, str]) -> dict:
    delays = (2.0, 5.0, 15.0)
    for attempt in range(len(delays) + 1):
        url = f"{BASE_URL}{path}?{urlencode(params)}"
        request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
        try:
            with urlopen(request, timeout=45) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read()
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            if exc.code != 429 and payload.get("code") != "50011":
                raise
            if attempt == len(delays):
                raise RuntimeError(f"OKX {path} rate limit exhausted: HTTP {exc.code} {payload}") from exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                wait = max(delays[attempt], float(retry_after or 0))
            except ValueError:
                wait = delays[attempt]
            time.sleep(wait)
            continue
        if not isinstance(payload, dict):
            raise RuntimeError(f"OKX {path} returned non-object JSON")
        if payload.get("code") == "50011":
            if attempt == len(delays):
                raise RuntimeError(f"OKX {path} rate limit exhausted: {payload}")
            time.sleep(delays[attempt])
            continue
        if payload.get("code") != "0":
            raise RuntimeError(f"OKX {path} failed: code={payload.get('code')} msg={payload.get('msg')}")
        return payload
    raise RuntimeError(f"OKX {path}: retry loop exhausted")


def month_bounds_hk(month: str) -> tuple[int, int]:
    try:
        year_text, month_text = month.split("-", 1)
        year, month_number = int(year_text), int(month_text)
        start = datetime(year, month_number, 1, tzinfo=HK_TZ)
        if month_number == 12:
            next_start = datetime(year + 1, 1, 1, tzinfo=HK_TZ)
        else:
            next_start = datetime(year, month_number + 1, 1, tzinfo=HK_TZ)
    except (ValueError, TypeError) as exc:
        raise ValueError("month must be YYYY-MM") from exc
    return int(start.timestamp() * 1000), int(next_start.timestamp() * 1000) - 1


def _archive_files(
    payload: dict, month: str, symbols: tuple[str, ...], file_keyword: str = "candlesticks"
) -> dict[str, dict]:
    data = payload.get("data")
    if isinstance(data, dict):
        details = data.get("details") or data.get("list")
        if details is None and "groupDetails" in data:
            details = [data]
    elif isinstance(data, list):
        details = []
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("details"), list):
                details.extend(item["details"])
            else:
                details.append(item)
    else:
        raise ValueError(
            f"OKX market-data-history response data has unsupported shape: "
            f"{type(data).__name__} {str(data)[:500]}"
        )
    if not isinstance(details, list):
        raise ValueError(
            f"OKX market-data-history response has no details array: {str(data)[:500]}"
        )
    expected = {f"{symbol}-USDT" for symbol in symbols}
    files: dict[str, dict] = {}
    for detail in details:
        if not isinstance(detail, dict):
            continue
        family = str(detail.get("instFamily") or "")
        group_items = detail.get("groupDetails")
        if not isinstance(group_items, list):
            group_items = [detail] if detail.get("filename") and detail.get("url") else []
        for item in group_items:
            if not isinstance(item, dict):
                continue
            filename = str(item.get("filename") or "")
            url = str(item.get("url") or "")
            url_name = urlparse(url).path.rsplit("/", 1)[-1]
            if not filename or not url or filename != url_name:
                raise ValueError(f"OKX archive filename/url mismatch: {filename!r} != {url_name!r}")
            if file_keyword.lower() not in filename.lower() or month not in filename:
                continue
            for family_key in expected:
                if family == family_key or filename.startswith(f"{family_key}-"):
                    files[family_key] = item
    missing = sorted(expected - set(files))
    if missing:
        raise RuntimeError(
            f"OKX archive listing omitted expected families for {month}: {missing}; "
            f"response sample={str(data)[:1000]}"
        )
    return files


def _decimal(value: str, field: str) -> Decimal:
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if not number.is_finite():
        raise ValueError(f"non-finite {field}: {value!r}")
    return number


def parse_candle_zip(blob: bytes, inst_id: str, begin_ms: int, end_ms: int) -> tuple[list[dict], dict]:
    bars: dict[int, dict] = {}
    duplicate_count = 0
    csv_members = 0
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for member in archive.namelist():
            if member.endswith("/") or not member.lower().endswith(".csv"):
                continue
            csv_members += 1
            with archive.open(member) as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
                for line_number, row in enumerate(reader, start=1):
                    if not row:
                        continue
                    if len(row) < 10:
                        raise ValueError(f"{member}:{line_number}: expected 10 CSV fields, got {len(row)}")
                    if not row[8].strip().isdigit():
                        if line_number == 1:
                            continue
                        raise ValueError(f"{member}:{line_number}: invalid open_time {row[8]!r}")
                    if row[0] != inst_id:
                        continue
                    timestamp = int(row[8])
                    if not begin_ms <= timestamp <= end_ms:
                        continue
                    bar = {
                        "open_time": timestamp,
                        "open": str(_decimal(row[1], "open")),
                        "high": str(_decimal(row[2], "high")),
                        "low": str(_decimal(row[3], "low")),
                        "close": str(_decimal(row[4], "close")),
                        "volume_contracts": str(_decimal(row[5], "volume")),
                        "volume_base": "0" if row[6] in {"", "None"} else str(_decimal(row[6], "volume base")),
                        "volume_quote": "0" if row[7] in {"", "None"} else str(_decimal(row[7], "volume quote")),
                        "confirm": row[9],
                        "source": "okx_monthly_archive",
                    }
                    if Decimal(bar["high"]) < max(Decimal(bar["open"]), Decimal(bar["close"]), Decimal(bar["low"])):
                        raise ValueError(f"{member}:{line_number}: invalid OHLC high")
                    if Decimal(bar["low"]) > min(Decimal(bar["open"]), Decimal(bar["close"]), Decimal(bar["high"])):
                        raise ValueError(f"{member}:{line_number}: invalid OHLC low")
                    previous = bars.get(timestamp)
                    if previous is not None:
                        if previous != bar:
                            raise ValueError(f"conflicting duplicate OKX candle {inst_id} at {timestamp}")
                        duplicate_count += 1
                    else:
                        bars[timestamp] = bar
    if not csv_members:
        raise ValueError("OKX ZIP contains no CSV members")
    ordered = [bars[key] for key in sorted(bars)]
    times = [int(bar["open_time"]) for bar in ordered]
    gaps = [
        {"after": left, "before": right, "missing_minutes": (right - left) // 60_000 - 1}
        for left, right in itertools.pairwise(times)
        if right - left > 60_000
    ]
    summary = {
        "inst_id": inst_id,
        "bar_count": len(ordered),
        "first_open_time": times[0] if times else None,
        "last_open_time": times[-1] if times else None,
        "duplicate_rows_identical": duplicate_count,
        "gap_count": len(gaps),
        "gap_samples": gaps[:20],
        "zero_volume_bars": sum(Decimal(bar["volume_contracts"]) == 0 for bar in ordered),
        "all_confirm_flags": sorted({bar["confirm"] for bar in ordered}),
        "csv_members": csv_members,
    }
    if not ordered:
        raise RuntimeError(f"OKX ZIP contained no {inst_id} candles")
    return ordered, summary


def fetch_rest_candle(inst_id: str, timestamp_ms: int) -> dict | None:
    """Fetch one historical 1m candle to verify/patch a monthly archive gap."""
    payload = _request_json(
        "/api/v5/market/history-candles",
        {
            "instId": inst_id,
            "bar": "1m",
            "after": str(timestamp_ms + 60_000),
            "before": str(timestamp_ms - 60_000),
            "limit": "10",
        },
    )
    for row in payload.get("data") or []:
        if len(row) < 9 or int(row[0]) != timestamp_ms:
            continue
        return {
            "open_time": timestamp_ms,
            "open": str(_decimal(row[1], "open")),
            "high": str(_decimal(row[2], "high")),
            "low": str(_decimal(row[3], "low")),
            "close": str(_decimal(row[4], "close")),
            "volume_contracts": str(_decimal(row[5], "volume")),
            "volume_base": "0" if row[6] in {"", "None"} else str(_decimal(row[6], "volume base")),
            "volume_quote": "0" if row[7] in {"", "None"} else str(_decimal(row[7], "volume quote")),
            "confirm": str(row[8]),
            "source": "okx_rest_history_candles_gap_patch",
        }
    return None


def patch_month_gaps(
    inst_id: str, month: str, summary: dict, out_dir: Path
) -> dict:
    """Patch archive omissions only when the official REST candle exists."""
    if not summary["gap_count"]:
        return summary
    csv_path = out_dir / month / f"{inst_id}_1m_{month}.csv"
    with csv_path.open(newline="", encoding="utf-8") as handle:
        bars = {int(row["open_time"]): row for row in csv.DictReader(handle)}
    patched = []
    unresolved = []
    for gap in summary["gap_samples"]:
        for timestamp in range(gap["after"] + 60_000, gap["before"], 60_000):
            rest_bar = fetch_rest_candle(inst_id, timestamp)
            if rest_bar is None:
                unresolved.append(timestamp)
            else:
                bars[timestamp] = rest_bar
                patched.append(timestamp)
            time.sleep(0.25)
    ordered = [bars[key] for key in sorted(bars)]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ordered[0]))
        writer.writeheader()
        writer.writerows(ordered)
    times = [int(bar["open_time"]) for bar in ordered]
    gaps = [
        {"after": left, "before": right, "missing_minutes": (right - left) // 60_000 - 1}
        for left, right in itertools.pairwise(times)
        if right - left > 60_000
    ]
    summary.update({
        "bar_count": len(ordered),
        "first_open_time": times[0] if times else None,
        "last_open_time": times[-1] if times else None,
        "gap_count": len(gaps),
        "gap_samples": gaps[:20],
        "rest_gap_patches": patched,
        "unresolved_gap_timestamps": unresolved,
        "zero_volume_bars": sum(Decimal(bar["volume_contracts"]) == 0 for bar in ordered),
    })
    month_manifest_path = out_dir / month / "manifest.json"
    if month_manifest_path.exists():
        month_manifest = json.loads(month_manifest_path.read_text(encoding="utf-8"))
        month_manifest["symbols"][inst_id] = summary
        month_manifest_path.write_text(
            json.dumps(month_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return summary


def fetch_funding(inst_id: str, begin_ms: int, end_ms: int) -> tuple[list[dict], dict]:
    payload = _request_json(
        "/api/v5/public/funding-rate-history",
        {
            "instId": inst_id,
            "after": str(end_ms + 1),
            "before": str(begin_ms - 1),
            "limit": "400",
        },
    )
    data = payload.get("data") or []
    rates = []
    seen = set()
    for row in data:
        ts = int(row["fundingTime"])
        if not begin_ms <= ts <= end_ms:
            continue
        if ts in seen:
            continue
        seen.add(ts)
        rates.append({
            "funding_time": ts,
            "funding_rate": row.get("fundingRate"),
            "realized_rate": row.get("realizedRate"),
            "method": row.get("method"),
        })
    rates.sort(key=lambda item: item["funding_time"])
    diffs = [b["funding_time"] - a["funding_time"] for a, b in itertools.pairwise(rates)]
    summary = {
        "inst_id": inst_id,
        "funding_events": len(rates),
        "first_funding_time": rates[0]["funding_time"] if rates else None,
        "last_funding_time": rates[-1]["funding_time"] if rates else None,
        "max_gap_hours": max(diffs) / 3_600_000 if diffs else None,
        "realized_rate_available": sum(item["realized_rate"] not in (None, "") for item in rates),
    }
    if not rates:
        raise RuntimeError(f"OKX funding history returned no rows for {inst_id}")
    return rates, summary


def parse_funding_zip(blob: bytes, inst_id: str, begin_ms: int, end_ms: int) -> tuple[list[dict], dict]:
    rates: dict[int, dict] = {}
    duplicate_count = 0
    csv_members = 0
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for member in archive.namelist():
            if member.endswith("/") or not member.lower().endswith(".csv"):
                continue
            csv_members += 1
            with archive.open(member) as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
                for line_number, row in enumerate(reader, start=1):
                    if not row:
                        continue
                    if len(row) < 3:
                        raise ValueError(f"{member}:{line_number}: expected at least 3 funding fields")
                    time_index = 3 if len(row) >= 4 else 2
                    if not row[time_index].strip().isdigit():
                        if line_number == 1:
                            continue
                        raise ValueError(f"{member}:{line_number}: invalid funding timestamp")
                    if row[0] != inst_id:
                        continue
                    timestamp = int(row[time_index])
                    if not begin_ms <= timestamp <= end_ms or row[1] in {"", "None"}:
                        continue
                    rate = str(_decimal(row[1], "funding rate"))
                    item = {"funding_time": timestamp, "funding_rate": rate}
                    previous = rates.get(timestamp)
                    if previous is not None:
                        if previous != item:
                            raise ValueError(f"conflicting duplicate OKX funding rate {inst_id} at {timestamp}")
                        duplicate_count += 1
                    else:
                        rates[timestamp] = item
    if not csv_members:
        raise ValueError("OKX funding ZIP contains no CSV members")
    ordered = [rates[key] for key in sorted(rates)]
    times = [item["funding_time"] for item in ordered]
    diffs = [right - left for left, right in itertools.pairwise(times)]
    summary = {
        "inst_id": inst_id,
        "funding_events": len(ordered),
        "first_funding_time": times[0] if times else None,
        "last_funding_time": times[-1] if times else None,
        "max_gap_hours": max(diffs) / 3_600_000 if diffs else None,
        "duplicate_rows_identical": duplicate_count,
        "csv_members": csv_members,
    }
    if not ordered:
        raise RuntimeError(f"OKX funding ZIP contained no {inst_id} events")
    return ordered, summary


def fetch_funding_archive(
    family: str, inst_id: str, month: str, begin_ms: int, end_ms: int
) -> tuple[list[dict], dict]:
    payload = _request_json(
        ARCHIVE_PATH,
        {
            "module": "3",
            "instType": "SWAP",
            "instFamilyList": family,
            "dateAggrType": "monthly",
            "begin": str(begin_ms),
            "end": str(end_ms),
        },
    )
    files = _archive_files(payload, month, (family.removesuffix("-USDT"),), file_keyword="funding")
    item = files[family]
    request = Request(item["url"], headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=120) as response:
        blob = response.read()
    digest = hashlib.sha256(blob).hexdigest()
    rates, summary = parse_funding_zip(blob, inst_id, begin_ms, end_ms)
    summary.update({
        "archive_filename": item["filename"],
        "archive_size_mb": item.get("sizeMB"),
        "archive_sha256": digest,
    })
    return rates, summary


def run_probe(month: str, out_dir: Path, base_symbols: tuple[str, ...] = SYMBOLS) -> dict:
    begin_ms, end_ms = month_bounds_hk(month)
    symbols = tuple(f"{symbol}-USDT" for symbol in base_symbols)
    params = {
        "module": "2",
        "instType": "SWAP",
        "instFamilyList": ",".join(symbols),
        "dateAggrType": "monthly",
        "begin": str(begin_ms),
        "end": str(end_ms),
    }
    payload = None
    last_error = None
    files = None
    for attempt in range(8):
        try:
            payload = _request_json(ARCHIVE_PATH, params)
            files = _archive_files(payload, month, base_symbols)
            break
        except (RuntimeError, ValueError) as exc:
            last_error = str(exc)
            if attempt == 7:
                raise RuntimeError(f"OKX archive listing did not stabilize: {last_error}") from exc
            time.sleep(1.0 + attempt)
    if files is None:
        raise RuntimeError(f"OKX archive listing unavailable: {last_error}")

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "exchange": "OKX",
        "market": "USDT-margined perpetual swaps",
        "month": month,
        "month_timezone": "UTC+8 archive month",
        "begin_ms_inclusive": begin_ms,
        "end_ms_inclusive": end_ms,
        "archive_endpoint": f"{BASE_URL}{ARCHIVE_PATH}",
        "archive_total_size_mb": (payload.get("data", {}).get("totalSizeMB") if isinstance(payload.get("data"), dict) else None),
        "symbols": {},
        "limitations": [
            "OKX data is a cross-venue robustness source, not BingX execution data.",
            "The archive is 1m candles; 15m/1h bars must be aggregated from complete closed 1m groups.",
            "Funding archive and REST overlap are both collected; archive/realizedRate agreement is measured before historical use.",
            "No fills, user identities, or per-wallet PnL are inferred from market data.",
        ],
    }
    for family, item in files.items():
        inst_id = f"{family}-SWAP"
        request = Request(item["url"], headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=120) as response:
            blob = response.read()
        digest = hashlib.sha256(blob).hexdigest()
        bars, summary = parse_candle_zip(blob, inst_id, begin_ms, end_ms)
        csv_path = out_dir / f"{inst_id}_1m_{month}.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(bars[0]))
            writer.writeheader()
            writer.writerows(bars)
        summary.update({
            "archive_filename": item["filename"],
            "archive_size_mb": item.get("sizeMB"),
            "archive_sha256": digest,
            "archive_csv": str(csv_path),
        })
        time.sleep(0.25)
        rest_cutoff_ms = int((datetime.now(UTC).timestamp() - 90 * 24 * 60 * 60) * 1000)
        if end_ms < rest_cutoff_ms:
            rest_rates = []
            rest_funding_summary = {
                "inst_id": inst_id,
                "funding_events": 0,
                "skipped": "outside the OKX REST endpoint's recent ~3-month window",
            }
        else:
            rest_rates, rest_funding_summary = fetch_funding(inst_id, begin_ms, end_ms)
        time.sleep(0.25)
        archive_rates, archive_funding_summary = fetch_funding_archive(
            family, inst_id, month, begin_ms, end_ms
        )
        rest_realized = {
            int(row["funding_time"]): row.get("realized_rate")
            for row in rest_rates
        }
        archive_rate_map = {
            int(row["funding_time"]): row["funding_rate"]
            for row in archive_rates
        }
        overlap = sorted(set(rest_realized) & set(archive_rate_map))
        matched = sum(
            rest_realized[ts] not in (None, "")
            and abs(float(rest_realized[ts]) - float(archive_rate_map[ts])) <= 1e-12
            for ts in overlap
        )
        archive_funding_summary["rest_overlap_events"] = len(overlap)
        archive_funding_summary["archive_matches_rest_realized_rate"] = matched
        archive_funding_summary["archive_rest_mismatch_count"] = len(overlap) - matched
        funding_path = out_dir / f"{inst_id}_funding_archive_{month}.csv"
        with funding_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(archive_rates[0]))
            writer.writeheader()
            writer.writerows(archive_rates)
        archive_funding_summary["csv_path"] = str(funding_path)
        summary["funding"] = {
            "archive": archive_funding_summary,
            "rest_api": rest_funding_summary,
        }
        manifest["symbols"][inst_id] = summary
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def month_sequence(start_month: str, end_month: str) -> list[str]:
    """Return inclusive YYYY-MM months in chronological order."""
    month_bounds_hk(start_month)
    month_bounds_hk(end_month)
    start_year, start_number = (int(part) for part in start_month.split("-"))
    end_year, end_number = (int(part) for part in end_month.split("-"))
    start_index = start_year * 12 + start_number - 1
    end_index = end_year * 12 + end_number - 1
    if start_index > end_index:
        raise ValueError("start_month must be <= end_month")
    result = []
    for index in range(start_index, end_index + 1):
        year, month_number = divmod(index, 12)
        result.append(f"{year:04d}-{month_number + 1:02d}")
    return result


def run_probe_range(
    start_month: str,
    end_month: str,
    out_dir: Path,
    base_symbols: tuple[str, ...] = SYMBOLS,
) -> dict:
    """Download monthly OKX candles and settled funding, then enforce common coverage."""
    if not base_symbols or len(set(base_symbols)) != len(base_symbols):
        raise ValueError("symbols must be a non-empty unique tuple")
    unknown = sorted(set(base_symbols) - set(SYMBOLS))
    if unknown:
        raise ValueError(f"unsupported OKX research symbols: {unknown}")
    months = month_sequence(start_month, end_month)
    month_reports = {
        month: run_probe(month, out_dir / month, base_symbols)
        for month in months
    }
    for month in months:
        for inst_id, summary in month_reports[month]["symbols"].items():
            if summary["gap_count"]:
                month_reports[month]["symbols"][inst_id] = patch_month_gaps(
                    inst_id, month, summary, out_dir
                )
    expected_symbols = [f"{symbol}-USDT-SWAP" for symbol in base_symbols]
    first_begin, _ = month_bounds_hk(months[0])
    _, last_end = month_bounds_hk(months[-1])
    expected_minutes = (last_end + 1 - first_begin) // 60_000
    aggregate_symbols = {}
    for inst_id in expected_symbols:
        summaries = [month_reports[month]["symbols"][inst_id] for month in months]
        candle_gaps = sum(item["gap_count"] for item in summaries)
        boundary_gaps = sum(
            summaries[index + 1]["first_open_time"] - summaries[index]["last_open_time"] != 60_000
            for index in range(len(summaries) - 1)
        )
        total_bars = sum(item["bar_count"] for item in summaries)
        first_open = summaries[0]["first_open_time"]
        last_open = summaries[-1]["last_open_time"]
        funding_summaries = [item["funding"]["archive"] for item in summaries]
        funding_events = sum(item["funding_events"] for item in funding_summaries)
        funding_boundary_gaps = [
            (funding_summaries[index + 1]["first_funding_time"] - funding_summaries[index]["last_funding_time"]) / 3_600_000
            for index in range(len(funding_summaries) - 1)
        ]
        max_funding_gap = max(
            [item["max_gap_hours"] or 0 for item in funding_summaries]
            + funding_boundary_gaps
        )
        rest_overlap = sum(item["funding"]["archive"]["rest_overlap_events"] for item in summaries)
        rest_matches = sum(item["funding"]["archive"]["archive_matches_rest_realized_rate"] for item in summaries)
        rest_mismatches = sum(item["funding"]["archive"]["archive_rest_mismatch_count"] for item in summaries)
        if (
            total_bars != expected_minutes
            or candle_gaps != 0
            or boundary_gaps != 0
            or first_open != first_begin
            or last_open != last_end - 59_999
        ):
            raise RuntimeError(
                f"{inst_id}: incomplete {start_month}..{end_month} candle coverage; "
                f"bars={total_bars}/{expected_minutes}, internal_gaps={candle_gaps}, "
                f"boundary_gaps={boundary_gaps}, first={first_open}, last={last_open}"
            )
        if max_funding_gap > 8.0:
            raise RuntimeError(
                f"{inst_id}: funding archive gap exceeds 8h: {max_funding_gap:.2f}h"
            )
        if funding_events == 0:
            raise RuntimeError(f"{inst_id}: no monthly settled funding archive rows")
        aggregate_symbols[inst_id] = {
            "month_count": len(months),
            "first_open_time": first_open,
            "last_open_time": last_open,
            "one_minute_bars": total_bars,
            "expected_one_minute_bars": expected_minutes,
            "internal_candle_gaps": candle_gaps,
            "month_boundary_candle_gaps": boundary_gaps,
            "complete_15m_bars": total_bars // 15,
            "complete_1h_bars": total_bars // 60,
            "funding_events": funding_events,
            "max_funding_gap_hours": max_funding_gap,
            "rest_overlap_events": rest_overlap,
            "archive_matches_rest_realized_rate": rest_matches,
            "archive_rest_mismatch_count": rest_mismatches,
            "monthly_archive_sha256": [
                {
                    "month": month,
                    "candles": month_reports[month]["symbols"][inst_id]["archive_sha256"],
                    "funding": month_reports[month]["symbols"][inst_id]["funding"]["archive"]["archive_sha256"],
                }
                for month in months
            ],
        }
    aggregate = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "exchange": "OKX",
        "market": "USDT-margined perpetual swaps",
        "start_month": start_month,
        "end_month": end_month,
        "month_timezone": "UTC+8 archive month",
        "months": months,
        "common_window_start_ms": first_begin,
        "common_window_end_ms": last_end,
        "symbols": aggregate_symbols,
        "month_reports": month_reports,
        "limitations": [
            "OKX data is cross-venue robustness evidence, not BingX execution data.",
            "Only official monthly archive candles and exact REST gap patches are used; unresolved gaps fail the common-window gate.",
            "Per-wallet trading behaviour is not inferred from market candles or funding archives.",
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return aggregate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", default=None, help="Single OKX archive month in YYYY-MM, UTC+8")
    parser.add_argument("--start-month", default=None, help="Inclusive first month, YYYY-MM")
    parser.add_argument("--end-month", default=None, help="Inclusive last month, YYYY-MM")
    parser.add_argument("--out-dir", type=Path, default=Path("reports/okx_public_history_probe"))
    parser.add_argument(
        "--symbols",
        default=",".join(SYMBOLS),
        help="comma-separated OKX base symbols from the frozen research universe",
    )
    args = parser.parse_args()
    base_symbols = tuple(part.strip().upper() for part in args.symbols.split(",") if part.strip())
    if not base_symbols or len(set(base_symbols)) != len(base_symbols):
        parser.error("--symbols must contain unique comma-separated symbols")
    unknown = sorted(set(base_symbols) - set(SYMBOLS))
    if unknown:
        parser.error(f"unsupported --symbols: {unknown}")
    if args.start_month or args.end_month:
        if not args.start_month or not args.end_month or args.month:
            parser.error("provide both --start-month and --end-month, or only --month")
        report = run_probe_range(args.start_month, args.end_month, args.out_dir, base_symbols)
    else:
        report = run_probe(args.month or "2026-10", args.out_dir, base_symbols)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
