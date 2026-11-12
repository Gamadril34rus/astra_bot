from __future__ import annotations

import csv
import io
import json
import zipfile

import pytest
from scripts import probe_okx_public_history as probe


def _zip_csv(rows: list[list[str]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        text = io.StringIO()
        writer = csv.writer(text)
        writer.writerows(rows)
        archive.writestr("candles.csv", text.getvalue())
    return buffer.getvalue()


def test_month_bounds_use_okx_utc_plus_8_archive_calendar() -> None:
    begin, end = probe.month_bounds_hk("2026-10")
    assert begin == 1_790_784_000_000
    assert end == 1_793_462_399_999


def test_month_sequence_returns_inclusive_contiguous_months() -> None:
    assert probe.month_sequence("2025-11", "2026-02") == [
        "2025-11", "2025-12", "2026-01", "2026-02"
    ]


def test_month_sequence_rejects_reversed_range() -> None:
    with pytest.raises(ValueError, match="start_month must be"):
        probe.month_sequence("2026-02", "2025-11")


def test_okx_range_rejects_unknown_or_duplicate_symbol_cohorts(tmp_path) -> None:
    with pytest.raises(ValueError, match="unsupported OKX research symbols"):
        probe.run_probe_range("2025-01", "2025-01", tmp_path, ("BTC", "XRP"))
    with pytest.raises(ValueError, match="non-empty unique tuple"):
        probe.run_probe_range("2025-01", "2025-01", tmp_path, ())


def test_month_bounds_reject_invalid_month() -> None:
    with pytest.raises(ValueError, match="YYYY-MM"):
        probe.month_bounds_hk("2026-13")


def test_archive_listing_requires_filename_to_match_url_and_all_families() -> None:
    symbols = ("BTC", "ETH")
    details = []
    for symbol in symbols:
        family = f"{symbol}-USDT"
        filename = f"{family}-SWAP-candlesticks-2026-10.zip"
        details.append({
            "instFamily": family,
            "groupDetails": [{"filename": filename, "url": f"https://static.okx.com/{filename}"}],
        })
    result = probe._archive_files({"data": {"details": details}}, "2026-10", symbols)
    assert set(result) == {"BTC-USDT", "ETH-USDT"}


def test_archive_listing_flattens_okx_data_list_with_nested_details() -> None:
    symbols = ("BTC", "ETH")
    details = []
    for symbol in symbols:
        family = f"{symbol}-USDT"
        filename = f"{family}-SWAP-candlesticks-2026-10.zip"
        details.append({
            "instFamily": family,
            "groupDetails": [{"filename": filename, "url": f"https://static.okx.com/{filename}?v=1001"}],
        })
    result = probe._archive_files(
        {"data": [{"dateAggrType": "monthly", "details": details}]},
        "2026-10",
        symbols,
    )
    assert set(result) == {"BTC-USDT", "ETH-USDT"}


def test_archive_listing_supports_monthly_funding_file_names() -> None:
    filename = "BTC-USDT-SWAP-fundingRate-2026-10.zip"
    result = probe._archive_files(
        {
            "data": [{
                "details": [{
                    "instFamily": "BTC-USDT",
                    "groupDetails": [{
                        "filename": filename,
                        "url": f"https://static.okx.com/{filename}",
                    }],
                }],
            }],
        },
        "2026-10",
        ("BTC",),
        file_keyword="funding",
    )
    assert result["BTC-USDT"]["filename"] == filename


def test_parse_funding_zip_reads_three_column_archive_and_deduplicates() -> None:
    start = 1_790_784_000_000
    rows = [
        ["instId", "fundingRate", "fundingTime"],
        ["BTC-USDT-SWAP", "0.0001", str(start)],
        ["BTC-USDT-SWAP", "0.0002", str(start + 8 * 3_600_000)],
        ["BTC-USDT-SWAP", "0.0002", str(start + 8 * 3_600_000)],
    ]
    rates, summary = probe.parse_funding_zip(
        _zip_csv(rows),
        "BTC-USDT-SWAP",
        start,
        start + 8 * 3_600_000,
    )
    assert len(rates) == 2
    assert summary["duplicate_rows_identical"] == 1
    assert summary["max_gap_hours"] == 8.0


def test_archive_listing_rejects_foreign_url_filename() -> None:
    details = [{
        "instFamily": "BTC-USDT",
        "groupDetails": [{
            "filename": "BTC-USDT-SWAP-candlesticks-2026-10.zip",
            "url": "https://static.okx.com/ETH-USDT-SWAP-candlesticks-2026-10.zip",
        }],
    }]
    with pytest.raises(ValueError, match="filename/url mismatch"):
        probe._archive_files({"data": {"details": details}}, "2026-10", ("BTC",))


def test_parse_candle_zip_filters_instrument_and_deduplicates_identical_rows() -> None:
    start = 1_790_784_000_000
    rows = [
        ["instId", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "ts", "confirm"],
        ["BTC-USDT-SWAP", "100", "102", "99", "101", "2", "2", "202", str(start), "1"],
        ["ETH-USDT-SWAP", "200", "202", "199", "201", "3", "3", "603", str(start), "1"],
        ["BTC-USDT-SWAP", "101", "103", "100", "102", "1", "1", "102", str(start + 60_000), "0"],
        ["BTC-USDT-SWAP", "101", "103", "100", "102", "1", "1", "102", str(start + 60_000), "0"],
    ]
    bars, summary = probe.parse_candle_zip(_zip_csv(rows), "BTC-USDT-SWAP", start, start + 60_000)
    assert len(bars) == 2
    assert summary["duplicate_rows_identical"] == 1
    assert summary["gap_count"] == 0
    assert summary["all_confirm_flags"] == ["0", "1"]


def test_parse_candle_zip_rejects_conflicting_duplicate() -> None:
    start = 1_790_784_000_000
    rows = [
        ["instId", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "ts", "confirm"],
        ["BTC-USDT-SWAP", "100", "102", "99", "101", "2", "2", "202", str(start), "1"],
        ["BTC-USDT-SWAP", "100", "103", "99", "101", "2", "2", "202", str(start), "1"],
    ]
    with pytest.raises(ValueError, match="conflicting duplicate"):
        probe.parse_candle_zip(_zip_csv(rows), "BTC-USDT-SWAP", start, start)


def test_patch_month_gaps_inserts_only_verified_rest_bar(tmp_path, monkeypatch) -> None:
    month = "2026-09"
    inst_id = "SOL-USDT-SWAP"
    month_dir = tmp_path / month
    month_dir.mkdir()
    first = 1_788_250_000_000
    missing = first + 60_000
    last = first + 120_000
    csv_path = month_dir / f"{inst_id}_1m_{month}.csv"
    rows = [
        {
            "open_time": str(first), "open": "100", "high": "101", "low": "99",
            "close": "100", "volume_contracts": "1", "volume_base": "1",
            "volume_quote": "100", "confirm": "1", "source": "okx_monthly_archive",
        },
        {
            "open_time": str(last), "open": "101", "high": "102", "low": "100",
            "close": "101", "volume_contracts": "1", "volume_base": "1",
            "volume_quote": "101", "confirm": "1", "source": "okx_monthly_archive",
        },
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "gap_count": 1,
        "gap_samples": [{"after": first, "before": last, "missing_minutes": 1}],
        "bar_count": 2,
        "first_open_time": first,
        "last_open_time": last,
    }
    (month_dir / "manifest.json").write_text(
        json.dumps({"symbols": {inst_id: summary}}),
        encoding="utf-8",
    )
    patched_bar = {
        "open_time": missing, "open": "100", "high": "101", "low": "100",
        "close": "101", "volume_contracts": "0", "volume_base": "0",
        "volume_quote": "0", "confirm": "1",
        "source": "okx_rest_history_candles_gap_patch",
    }
    monkeypatch.setattr(probe, "fetch_rest_candle", lambda symbol, ts: patched_bar)
    monkeypatch.setattr(probe.time, "sleep", lambda _: None)

    updated = probe.patch_month_gaps(inst_id, month, summary, tmp_path)

    assert updated["bar_count"] == 3
    assert updated["gap_count"] == 0
    assert updated["rest_gap_patches"] == [missing]
    with csv_path.open(newline="", encoding="utf-8") as handle:
        saved = list(csv.DictReader(handle))
    assert len(saved) == 3
    assert saved[1]["source"] == "okx_rest_history_candles_gap_patch"


def test_rest_gap_patch_fetches_exact_historical_minute(monkeypatch) -> None:
    timestamp = 1_790_784_000_000
    captured = {}

    def fake_request(path, params):
        captured.update(path=path, params=params)
        return {
            "code": "0",
            "data": [
                [str(timestamp - 60_000), "99", "101", "98", "100", "1", "1", "100", "1"],
                [str(timestamp), "100", "102", "99", "101", "0", "0", "0", "1"],
                [str(timestamp + 60_000), "101", "103", "100", "102", "1", "1", "102", "1"],
            ],
        }

    monkeypatch.setattr(probe, "_request_json", fake_request)
    result = probe.fetch_rest_candle("SOL-USDT-SWAP", timestamp)

    assert captured["path"] == "/api/v5/market/history-candles"
    assert captured["params"]["after"] == str(timestamp + 60_000)
    assert captured["params"]["before"] == str(timestamp - 60_000)
    assert result["open_time"] == timestamp
    assert result["volume_contracts"] == "0"
    assert result["source"] == "okx_rest_history_candles_gap_patch"


def test_funding_history_uses_correct_before_after_window(monkeypatch) -> None:
    begin, end = 1_790_784_000_000, 1_793_462_399_999
    captured = {}

    def fake_request(path, params):
        captured.update(path=path, params=params)
        return {"code": "0", "data": [
            {"fundingTime": str(begin), "fundingRate": "0.001", "realizedRate": "0.0009"},
            {"fundingTime": str(end), "fundingRate": "0.001", "realizedRate": "0.0008"},
        ]}

    monkeypatch.setattr(probe, "_request_json", fake_request)
    rates, summary = probe.fetch_funding("BTC-USDT-SWAP", begin, end)
    assert captured["params"]["after"] == str(end + 1)
    assert captured["params"]["before"] == str(begin - 1)
    assert summary["funding_events"] == 2
    assert len(rates) == 2
