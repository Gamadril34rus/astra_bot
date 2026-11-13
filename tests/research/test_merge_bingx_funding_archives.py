from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest
from scripts.merge_bingx_funding_archives import merge_archives

SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
CORE = ("ETH-USDT", "SOL-USDT", "HYPE-USDT", "INJ-USDT")
START = 1_767_225_600_000  # 2026-01-01T00:00:00Z
END = START + 2 * 24 * 60 * 60 * 1000


def _write_source(
    directory: Path,
    rows_by_symbol: dict[str, list[tuple[int, str]]],
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    reports = []
    for symbol, rows in rows_by_symbol.items():
        path = directory / f"funding_{symbol.replace('-', '')}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("funding_time", "rate"))
            writer.writeheader()
            writer.writerows(
                {"funding_time": timestamp, "rate": rate}
                for timestamp, rate in rows
            )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        reports.append({
            "symbol": symbol,
            "file": str(path),
            "events": len(rows),
            "sha256": digest,
            "source": "BingX /openApi/swap/v2/quote/fundingRate",
        })
    (directory / "funding_manifest.json").write_text(
        json.dumps({
            "exchange": "BingX",
            "market": "USDT-M perpetual swaps",
            "reports": reports,
        }),
        encoding="utf-8",
    )


def _fixture_rows() -> dict[str, list[tuple[int, str]]]:
    result = {}
    for symbol in SYMBOLS:
        interval = 4 * 60 * 60 * 1000 if symbol == "HYPE-USDT" else 8 * 60 * 60 * 1000
        result[symbol] = [
            (timestamp, "0.0001")
            for timestamp in range(START, END + 1, interval)
        ]
    return result


def test_merge_accepts_verified_union_and_recognizes_symbol_cadence(tmp_path: Path) -> None:
    all_rows = _fixture_rows()
    base_rows = {
        symbol: rows[: max(2, len(rows) // 2)]
        for symbol, rows in all_rows.items()
    }
    supplement_rows = {
        symbol: rows[max(2, len(rows) // 2) - 1 :]
        for symbol, rows in all_rows.items()
    }
    base = tmp_path / "base"
    supplement = tmp_path / "supplement"
    _write_source(base, base_rows)
    _write_source(supplement, supplement_rows)

    manifest = merge_archives(
        base,
        supplement,
        tmp_path / "merged",
        symbols=SYMBOLS,
        core_symbols=CORE,
        requested_start="2026-01-01T00:00:00Z",
        requested_end="2026-01-03T00:00:00Z",
        require_days=2.0,
    )

    assert manifest["core_common_window"]["covers_required_days"] is True
    assert manifest["core_common_window"]["days"] == pytest.approx(2.0)
    reports = {row["symbol"]: row for row in manifest["reports"]}
    assert reports["HYPE-USDT"]["median_gap_hours"] == pytest.approx(4.0)
    assert reports["ETH-USDT"]["median_gap_hours"] == pytest.approx(8.0)
    assert reports["BTC-USDT"]["max_gap_hours_latest_segment"] == pytest.approx(8.0)


def test_merge_rejects_a_conflicting_duplicate_rate(tmp_path: Path) -> None:
    rows = _fixture_rows()
    base_rows = {symbol: values[:3] for symbol, values in rows.items()}
    supplement_rows = {symbol: values[2:] for symbol, values in rows.items()}
    supplement_rows["ETH-USDT"][0] = (supplement_rows["ETH-USDT"][0][0], "0.0002")
    base = tmp_path / "base"
    supplement = tmp_path / "supplement"
    _write_source(base, base_rows)
    _write_source(supplement, supplement_rows)

    with pytest.raises(ValueError, match="conflicting BingX funding rate"):
        merge_archives(
            base,
            supplement,
            tmp_path / "merged",
            symbols=SYMBOLS,
            core_symbols=CORE,
            requested_start="2026-01-01T00:00:00Z",
            requested_end="2026-01-03T00:00:00Z",
            require_days=2.0,
        )


def test_merge_accepts_last_settlement_one_cadence_before_exclusive_end(tmp_path: Path) -> None:
    rows = _fixture_rows()
    rows["INJ-USDT"] = rows["INJ-USDT"][:-1]
    base_rows = {symbol: values[:3] for symbol, values in rows.items()}
    supplement_rows = {symbol: values[2:] for symbol, values in rows.items()}
    base = tmp_path / "base"
    supplement = tmp_path / "supplement"
    _write_source(base, base_rows)
    _write_source(supplement, supplement_rows)

    manifest = merge_archives(
        base,
        supplement,
        tmp_path / "merged",
        symbols=SYMBOLS,
        core_symbols=CORE,
        requested_start="2026-01-01T00:00:00Z",
        requested_end="2026-01-03T00:00:00Z",
        require_days=2.0,
    )
    assert manifest["core_common_window"]["covers_required_days"] is True
    assert manifest["core_common_window"]["days"] == pytest.approx(2.0)


def test_merge_rejects_missing_funding_at_the_requested_end(tmp_path: Path) -> None:
    rows = _fixture_rows()
    rows["INJ-USDT"] = rows["INJ-USDT"][:-2]
    base_rows = {symbol: values[:3] for symbol, values in rows.items()}
    supplement_rows = {symbol: values[2:] for symbol, values in rows.items()}
    base = tmp_path / "base"
    supplement = tmp_path / "supplement"
    _write_source(base, base_rows)
    _write_source(supplement, supplement_rows)

    with pytest.raises(RuntimeError, match="unresolved gaps"):
        merge_archives(
            base,
            supplement,
            tmp_path / "merged",
            symbols=SYMBOLS,
            core_symbols=CORE,
            requested_start="2026-01-01T00:00:00Z",
            requested_end="2026-01-03T00:00:00Z",
            require_days=2.0,
        )


def test_merge_rejects_a_gap_inside_latest_funding_segment(tmp_path: Path) -> None:
    rows = _fixture_rows()
    # Remove one INJ settlement while preserving later rows.
    rows["INJ-USDT"] = [row for row in rows["INJ-USDT"] if row[0] != START + 16 * 60 * 60 * 1000]
    base_rows = {symbol: values[:3] for symbol, values in rows.items()}
    supplement_rows = {symbol: values[2:] for symbol, values in rows.items()}
    base = tmp_path / "base"
    supplement = tmp_path / "supplement"
    _write_source(base, base_rows)
    _write_source(supplement, supplement_rows)

    with pytest.raises(RuntimeError, match="unresolved gaps"):
        merge_archives(
            base,
            supplement,
            tmp_path / "merged",
            symbols=SYMBOLS,
            core_symbols=CORE,
            requested_start="2026-01-01T00:00:00Z",
            requested_end="2026-01-03T00:00:00Z",
            require_days=2.0,
        )


def test_merge_verifies_source_hashes(tmp_path: Path) -> None:
    rows = _fixture_rows()
    base_rows = {symbol: values[:3] for symbol, values in rows.items()}
    supplement_rows = {symbol: values[2:] for symbol, values in rows.items()}
    base = tmp_path / "base"
    supplement = tmp_path / "supplement"
    _write_source(base, base_rows)
    _write_source(supplement, supplement_rows)
    path = base / "funding_BTCUSDT.csv"
    path.write_text(path.read_text(encoding="utf-8") + "999,0.0001\n", encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        merge_archives(
            base,
            supplement,
            tmp_path / "merged",
            symbols=SYMBOLS,
            core_symbols=CORE,
            requested_start="2026-01-01T00:00:00Z",
            requested_end="2026-01-03T00:00:00Z",
            require_days=2.0,
        )
