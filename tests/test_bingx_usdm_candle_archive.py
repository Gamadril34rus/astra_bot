undefined

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
            self.ranges: list[int] = []

        async def fetch_kline_page(self, _symbol, _timeframe, *, end_time_ms, limit):
            self.ranges.append(end_time_ms)
            page = [
                candle for candle in candles
                if candle.open_time <= end_time_ms
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
    assert all(row["taker_buy_volume"] == "6" for row in enriched.values())
    assert all(row["taker_buy_quote_volume"] == "600" for row in enriched.values())
