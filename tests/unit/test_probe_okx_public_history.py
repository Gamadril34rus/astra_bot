from __future__ import annotations

import csv
import io
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
