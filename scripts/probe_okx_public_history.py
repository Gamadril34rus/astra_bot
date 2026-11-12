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


def _archive_files(payload: dict, month: str, symbols: tuple[str, ...]) -> dict[str, dict]:
    data = payload.get("data")
    if isinstance(data, dict):
        details = data.get("details") or data.get("list")
        if details is None and "groupDetails" in data:
            details = [data]
    elif isinstance(data, list):
        details = data
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
            if f"candlesticks-{month}" not in filename:
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
    times = [bar["open_time"] for bar in ordered]
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


def run_probe(month: str, out_dir: Path) -> dict:
    begin_ms, end_ms = month_bounds_hk(month)
    symbols = tuple(f"{symbol}-USDT" for symbol in SYMBOLS)
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
            files = _archive_files(payload, month, SYMBOLS)
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
            "Funding API history is limited to the latest three months; this probe covers one recent month only.",
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
        rates, funding_summary = fetch_funding(inst_id, begin_ms, end_ms)
        funding_path = out_dir / f"{inst_id}_funding_{month}.csv"
        with funding_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rates[0]))
            writer.writeheader()
            writer.writerows(rates)
        funding_summary["csv_path"] = str(funding_path)
        summary["funding"] = funding_summary
        manifest["symbols"][inst_id] = summary
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", default="2026-10", help="OKX archive month in YYYY-MM, UTC+8")
    parser.add_argument("--out-dir", type=Path, default=Path("reports/okx_public_history_probe"))
    args = parser.parse_args()
    report = run_probe(args.month, args.out_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
