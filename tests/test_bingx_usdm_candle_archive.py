from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.archive_bingx_usdm_candles import (
    DEFAULT_TIMEFRAMES,
    TIMEFRAME_MS,
    _append_conflicts,
    _coverage,
    _merge_rows,
    _read_rows,
    _resolve_timeframes,
    _write_rows,
)


def _row(open_time: int, close: str = "100") -> dict[str, str]:
    return {
        "open_time": str(open_time),
        "open_time_utc": "2026-01-01T00:00:00+00:00",
        "open": "99",
        "high": "101",
        "low": "98",
        "close": close,
        "volume": "10",
        "quote_volume": "1000",
        "trades_count": "3",
        "taker_buy_volume": "",
        "taker_buy_quote_volume": "",
    }

def test_merge_is_sorted_and_idempotent() -> None:
    existing = {0: _row(0), 60_000: _row(60_000)}
    incoming = {60_000: _row(60_000), 120_000: _row(120_000)}
    merged = _merge_rows(existing, incoming)
    assert list(merged) == [0, 60_000, 120_000]
    assert _merge_rows(merged, incoming) == merged

def test_merge_rejects_revised_historical_bar() -> None:
    existing = {60_000: _row(60_000, "100")}
    incoming = {60_000: _row(60_000, "100.01")}
    with pytest.raises(ValueError, match="immutable candle conflict"):
        _merge_rows(existing, incoming)


def test_merge_quarantines_conflicting_bar_without_overwriting(tmp_path: Path) -> None:
    existing_row = _row(60_000, "100")
    incoming_row = _row(60_000, "100.01")
    conflicts: list[dict] = []
    merged = _merge_rows(
        {60_000: existing_row},
        {60_000: incoming_row, 120_000: _row(120_000)},
        conflicts=conflicts,
    )
    assert merged[60_000] == existing_row
    assert 120_000 in merged
    assert len(conflicts) == 1
    assert conflicts[0]["open_time"] == 60_000
    assert conflicts[0]["incoming"] == incoming_row


def test_conflict_audit_is_deduplicated(tmp_path: Path) -> None:
    conflict = {
        "open_time": 60_000,
        "existing": _row(60_000, "100"),
        "incoming": _row(60_000, "100.01"),
    }
    path = tmp_path / "conflicts" / "BTCUSDT_30m.jsonl"
    assert _append_conflicts(path, [conflict]) == 1
    assert _append_conflicts(path, [conflict]) == 0
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["open_time"] == 60_000
    assert rows[0]["conflict_id"]

def test_csv_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "candles.csv"
    rows = {0: _row(0), 60_000: _row(60_000)}
    _write_rows(path, rows)
    assert _read_rows(path) == rows

def test_coverage_counts_missing_intervals() -> None:
    rows = {0: _row(0), 15 * 60_000: _row(15 * 60_000), 45 * 60_000: _row(45 * 60_000)}
    coverage = _coverage(rows, TIMEFRAME_MS["15m"])
    assert coverage["bar_count"] == 3
    assert coverage["gap_count"] == 1
    assert coverage["gap_samples"][0]["missing_bars"] == 1

def test_merge_tolerates_optional_trade_count_shape_difference() -> None:
    existing_row = _row(60_000)
    incoming_row = _row(60_000)
    existing_row["trades_count"] = "0"
    incoming_row["trades_count"] = "42"
    assert _merge_rows({60_000: existing_row}, {60_000: incoming_row}) == {
        60_000: existing_row
    }

def test_daily_timeframes_follow_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"timeframes": ["1m", "5m"]}), encoding="utf-8")
    assert _resolve_timeframes(None, "daily", manifest) == ("1m", "5m")

def test_initial_timeframes_default_to_active_zeus_set(tmp_path: Path) -> None:
    assert _resolve_timeframes(None, "initial", tmp_path / "missing.json") == DEFAULT_TIMEFRAMES



def test_parse_bingx_kline_keeps_taker_buy_volumes() -> None:
    from decimal import Decimal

    from astra_bot.adapters.bingx.client import BingXClient

    client = object.__new__(BingXClient)
    candle = client._parse_candle(
        [
            1_800_000, "100", "102", "99", "101", "10",
            3_599_999, "1000", 25, "6.5", "650",
        ],
        "BTC-USDT",
        "30m",
    )

    assert candle is not None
    assert candle.trades_count == 25
    assert candle.taker_buy_volume == Decimal("6.5")
    assert candle.taker_buy_quote_volume == Decimal("650")


def test_parse_bingx_kline_marks_missing_taker_volumes_unknown() -> None:
    from astra_bot.adapters.bingx.client import BingXClient

    client = object.__new__(BingXClient)
    candle = client._parse_candle(
        [1_800_000, "100", "102", "99", "101", "10", 3_599_999, "1000"],
        "BTC-USDT",
        "30m",
    )

    assert candle is not None
    assert candle.taker_buy_volume is None
    assert candle.taker_buy_quote_volume is None


def test_legacy_archive_can_be_enriched_with_taker_buy_fields() -> None:
    previous = _row(60_000)
    incoming = _row(60_000)
    incoming["taker_buy_volume"] = "6.5"
    incoming["taker_buy_quote_volume"] = "650"

    merged = _merge_rows({60_000: previous}, {60_000: incoming})

    assert merged[60_000]["taker_buy_volume"] == "6.5"
    assert merged[60_000]["taker_buy_quote_volume"] == "650"


def test_taker_buy_volume_conflict_is_not_silently_overwritten() -> None:
    previous = _row(60_000)
    previous["taker_buy_volume"] = "6.5"
    previous["taker_buy_quote_volume"] = "650"
    incoming = _row(60_000)
    incoming["taker_buy_volume"] = "6.6"
    incoming["taker_buy_quote_volume"] = "660"

    with pytest.raises(ValueError, match="immutable candle conflict"):
        _merge_rows({60_000: previous}, {60_000: incoming})


@pytest.mark.asyncio
async def test_refresh_optional_fields_refetches_full_existing_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from decimal import Decimal
    from types import SimpleNamespace

    import scripts.archive_bingx_usdm_candles as archive

    monkeypatch.setattr(archive, "ARCHIVE_DIR", tmp_path)
    interval = archive.TIMEFRAME_MS["30m"]
    existing_rows = {
        index * interval: {
            **_row(index * interval),
            "taker_buy_volume": "",
            "taker_buy_quote_volume": "",
        }
        for index in range(4)
    }
    archive._write_rows(
        archive._archive_path("BTC-USDT", "30m"),
        existing_rows,
    )

    candles = [
        SimpleNamespace(
            open_time=index * interval,
            open=Decimal("99"),
            high=Decimal("101"),
            low=Decimal("98"),
            close=Decimal("100"),
            volume=Decimal("10"),
            quote_volume=Decimal("1000"),
            trades_count=10,
            taker_buy_volume=Decimal("6"),
            taker_buy_quote_volume=Decimal("600"),
        )
        for index in range(4)
    ]

    class FakeClient:
        def __init__(self) -> None:
            self.ranges: list[tuple[int, int]] = []

        async def fetch_kline_page(
            self, _symbol, _timeframe, *, start_time_ms, end_time_ms, limit
        ):
            self.ranges.append((start_time_ms, end_time_ms))
            page = [
                candle for candle in candles
                if start_time_ms <= candle.open_time <= end_time_ms
            ]
            return sorted(page, key=lambda item: item.open_time, reverse=True)[:limit]

    monkeypatch.setattr(archive, "PAGE_SIZE", 2)
    client = FakeClient()
    report, _ = await archive._download_pair(
        client,
        symbol="BTC-USDT",
        timeframe="30m",
        mode="daily",
        start_ms=0,
        now_ms=4 * interval + 1,
        refresh_optional_fields=True,
    )

    enriched = archive._read_rows(archive._archive_path("BTC-USDT", "30m"))
    assert report["verified_overlap_bars"] == 4
    assert len(client.ranges) == 2
    assert [start for start, _ in client.ranges] == [0, 2 * interval]
    assert all(row["taker_buy_volume"] == "6" for row in enriched.values())
    assert all(row["taker_buy_quote_volume"] == "600" for row in enriched.values())


@pytest.mark.asyncio
async def test_daily_without_manifest_fails_before_client_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    import scripts.archive_bingx_usdm_candles as archive

    monkeypatch.setattr(archive, "MANIFEST_PATH", tmp_path / "manifest.json")

    def unexpected_client(*_args, **_kwargs):
        raise AssertionError("BingX client must not be constructed without a daily manifest")

    monkeypatch.setattr(archive, "BingXClient", unexpected_client)
    args = SimpleNamespace(
        start_date="2021-01-01",
        mode="daily",
        symbols=("BTC-USDT",),
        timeframes=("5m",),
        refresh_optional_fields=False,
    )

    with pytest.raises(RuntimeError, match="missing initial archive manifest"):
        await archive.run(args)


def test_archive_workflow_guards_run_before_archive_import_or_download() -> None:
    workflow_path = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "bingx-usdm-candle-archive.yml"
    )
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "INITIATOR: ${{ github.actor }}" in workflow
    assert "REPOSITORY_OWNER: ${{ github.repository_owner }}" in workflow
    assert 'if [[ "$ARCHIVE_MODE" == "initial" && "$INITIATOR" != "$REPOSITORY_OWNER" ]]; then' in workflow
    assert 'if [[ "$ARCHIVE_MODE" == "daily" && ! -f data/bingx_usdm/manifest.json ]]; then' in workflow

    import_index = workflow.index("python -c 'from astra_bot.adapters.bingx.client")
    assert workflow.index('if [[ -z "$ARCHIVE_MODE" ]]') < import_index
    assert workflow.index('if [[ "$ARCHIVE_MODE" == "daily"') < import_index
    assert workflow.index('if [[ "$ARCHIVE_MODE" == "initial"') < import_index
