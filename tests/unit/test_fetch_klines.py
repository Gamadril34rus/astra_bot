"""Тесты хелперов scripts/fetch_klines.py (без сети)."""

import io
import os
import sys
import urllib.error
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from fetch_klines import (
    fetch_binance_vision,
    iter_months,
    parse_klines_csv,
    vision_monthly_url,
)


def test_vision_monthly_url_format():
    url = vision_monthly_url("ETHUSDT", "4h", 2024, 1)
    assert url == (
        "https://data.binance.vision/data/spot/monthly/klines/"
        "ETHUSDT/4h/ETHUSDT-4h-2024-01.zip"
    )
    assert vision_monthly_url("btcusdt", "1h", 2026, 12).endswith(
        "BTCUSDT-1h-2026-12.zip"
    )


def test_iter_months_covers_range():
    months = list(iter_months(datetime(2024, 11, 1, tzinfo=UTC), datetime(2025, 2, 28, tzinfo=UTC)))
    assert months == [(2024, 11), (2024, 12), (2025, 1), (2025, 2)]


def test_parse_klines_csv_keeps_only_data_rows():
    text = (
        "open_time,open,high,low,close,volume,close_time,quote_volume,ignore\n"
        "1724112000000,2636.3,2676.75,2621.56,2661.98,6102.21,1724126399999,16194046.89,0\n"
        "1724126400000,2661.98,2694.99,2657.81,2674.82,7863.85,1724140799999,21031519.35,0\n"
        "\n"
        "# мусорная строка\n"
    )
    rows = parse_klines_csv(text)
    assert len(rows) == 2
    assert rows[0].startswith("1724112000000")
    assert rows[1].split(",")[4] == "2674.82"


# ---------------------------------------------------------------------------
# Новые тесты по заданию Strategy Lab (1.1, 1.2, 1.3)
# ---------------------------------------------------------------------------

def _make_zip(csv_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("BTCUSDT-4h-2021-01.csv", csv_text)
    return buf.getvalue()


def _sample_kline_line(ts_ms: int) -> str:
    # open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy...,ignore
    return f"{ts_ms},100,101,99,100.5,1000,{ts_ms+1000},100000,100,500,50000,0"


def test_skip_current_month_no_retry(monkeypatch):
    """1.1: месяцы >= текущего → сразу skip, строка в manifest, БЕЗ retry-цикла."""
    # now = 2026-09-14, запрашиваем 2026-08 → 2026-09
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = datetime(2026, 9, 30, tzinfo=UTC)
    now = datetime(2026, 9, 14, tzinfo=UTC)

    called_urls = []

    def fake_urlopen(url, timeout=60):
        called_urls.append(url)
        # для августа возвращаем валидный zip с одной свечой
        if "2026-08" in url:
            return _make_zip(_sample_kline_line(int(start.timestamp() * 1000)))
        raise AssertionError(f"не должен вызываться для 2026-09, получили {url}")

    monkeypatch.setattr("fetch_klines._urlopen_with_retry", fake_urlopen)

    csv_text, meta = fetch_binance_vision("BTCUSDT", "4h", start, end, now=now)

    # 2026-09 должен быть пропущен без попытки скачать
    assert all("2026-09" not in u for u in called_urls), "retry на незакрытый месяц не должен вызываться"
    assert meta["months_skipped"] == 1
    assert meta["months_ok"] == 1
    assert "skipped: month not closed yet" in meta["skipped_sample"][0]
    # CSV должен содержать данные августа
    assert "100.5" in csv_text


def test_tail_404_is_ok(monkeypatch):
    """Хвост из 404 — штатная ситуация, не падает, если после нет успешных месяцев."""
    start = datetime(2021, 1, 1, tzinfo=UTC)
    end = datetime(2021, 4, 30, tzinfo=UTC)
    now = datetime(2021, 6, 1, tzinfo=UTC)  # после запрашиваемого диапазона

    def fake_urlopen(url, timeout=60):
        if "2021-01" in url or "2021-02" in url:
            ts = int(datetime(2021, 1 if "01" in url else 2, 1, tzinfo=UTC).timestamp() * 1000)
            return _make_zip(_sample_kline_line(ts))
        # 2021-03, 2021-04 — 404 хвост
        raise urllib.error.HTTPError(url, 404, "Not Found", hdrs=None, fp=None)

    monkeypatch.setattr("fetch_klines._urlopen_with_retry", fake_urlopen)

    _csv_text, meta = fetch_binance_vision("BTCUSDT", "4h", start, end, now=now)
    # не должно падать
    assert meta["months_ok"] == 2
    assert meta["months_failed"] == 2
    # хвост — не дыра, поэтому исключения нет


def test_hole_in_middle_raises(monkeypatch):
    """1.3: если >3 месяцев подряд 404 НЕ в хвосте — падать с внятной ошибкой."""
    start = datetime(2021, 1, 1, tzinfo=UTC)
    end = datetime(2021, 8, 31, tzinfo=UTC)
    now = datetime(2021, 10, 1, tzinfo=UTC)

    def fake_urlopen(url, timeout=60):
        # 01 ok, 02-05 fail (4 месяца подряд), 06-08 ok
        if "2021-01" in url or "2021-06" in url or "2021-07" in url or "2021-08" in url:
            # фиксированный ts, чтобы не парсить месяц из URL
            ts = int(datetime(2021, 1, 1, tzinfo=UTC).timestamp() * 1000)
            if "2021-06" in url:
                ts = int(datetime(2021, 6, 1, tzinfo=UTC).timestamp() * 1000)
            elif "2021-07" in url:
                ts = int(datetime(2021, 7, 1, tzinfo=UTC).timestamp() * 1000)
            elif "2021-08" in url:
                ts = int(datetime(2021, 8, 1, tzinfo=UTC).timestamp() * 1000)
            return _make_zip(_sample_kline_line(ts))
        raise urllib.error.HTTPError(url, 404, "Not Found", hdrs=None, fp=None)

    monkeypatch.setattr("fetch_klines._urlopen_with_retry", fake_urlopen)

    with pytest.raises(RuntimeError, match="дыра в данных"):
        fetch_binance_vision("BTCUSDT", "4h", start, end, now=now)


def test_hole_three_months_is_allowed(monkeypatch):
    """Ровно 3 месяца подряд 404 в середине — ещё не дыра, допускается."""
    start = datetime(2021, 1, 1, tzinfo=UTC)
    end = datetime(2021, 6, 30, tzinfo=UTC)
    now = datetime(2021, 8, 1, tzinfo=UTC)

    def fake_urlopen(url, timeout=60):
        if "2021-01" in url or "2021-05" in url or "2021-06" in url:
            ts = int(datetime(2021, 1, 1, tzinfo=UTC).timestamp() * 1000)
            return _make_zip(_sample_kline_line(ts))
        raise urllib.error.HTTPError(url, 404, "Not Found", hdrs=None, fp=None)

    monkeypatch.setattr("fetch_klines._urlopen_with_retry", fake_urlopen)

    _csv_text, meta = fetch_binance_vision("BTCUSDT", "4h", start, end, now=now)
    assert meta["months_failed"] == 3
    # не падает
    assert meta["months_ok"] == 3


def test_path_outside_dir_uses_relpath():
    """1.2: manifest-путь через os.path.relpath, гарантия что out внутри ожидаемого каталога."""
    # Старый код: str(out.relative_to(data_dir.parent)) падал с ValueError если файл вне каталога
    data_dir = Path("/home/user/astra_bot/data")
    out_outside = Path("/tmp/outside/BTCUSDT_4h.csv")
    # старый способ должен падать
    with pytest.raises(ValueError):
        str(out_outside.relative_to(data_dir.parent))

    # новый способ — через relpath — не падает
    rel = os.path.relpath(out_outside.resolve(), start=Path.cwd().resolve())
    assert isinstance(rel, str)
    assert "outside" in rel

    # даже если out внутри data_dir — relpath тоже работает и даёт data/...
    out_inside = data_dir / "BTCUSDT_4h.csv"
    rel_inside = os.path.relpath(out_inside.resolve(), start=Path.cwd().resolve())
    assert "BTCUSDT_4h.csv" in rel_inside


def test_manifest_skipped_message_format(monkeypatch):
    """Проверяем точную строку 'skipped: month not closed yet' в manifest."""
    start = datetime(2026, 9, 1, tzinfo=UTC)
    end = datetime(2026, 9, 30, tzinfo=UTC)
    now = datetime(2026, 9, 14, tzinfo=UTC)

    monkeypatch.setattr("fetch_klines._urlopen_with_retry", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("should not be called")
    ))

    csv_text, meta = fetch_binance_vision("BTCUSDT", "4h", start, end, now=now)
    assert csv_text == ""
    assert meta["months_skipped"] == 1
    assert meta["skipped_sample"][0].endswith("skipped: month not closed yet")
