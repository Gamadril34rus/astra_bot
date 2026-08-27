#!/usr/bin/env python3
"""Выгрузка исторических свечей (klines) в формат data/BTCUSDT_*.csv.

Два источника (без API-ключей):
1. **Binance Vision** (data.binance.vision) — помесячные zip со спота
   Binance. Рекомендуемый источник: полная история, стабильные лимиты.
2. **MEXC REST** (fallback) — пагинированные запросы по 1000 свечей.

Формат вывода совместим с бэктестерами и лабораторией проекта:
    open_time,open,high,low,close,volume,close_time,quote_volume,ignore

Примеры:
    # ETH 4h за 2 года (для мульти-актив валидации портфеля):
    python scripts/fetch_klines.py --symbol ETHUSDT --timeframe 4h \\
        --start 2024-08-20 --end 2026-08-20 --out data/ETHUSDT_4h.csv

    # SOL 1h и 4h:
    python scripts/fetch_klines.py --symbol SOLUSDT --timeframe 1h --start 2021-01-01 --end 2026-08-20
    python scripts/fetch_klines.py --symbol SOLUSDT --timeframe 4h --start 2021-01-01 --end 2026-08-20

Запускать на машине, где доступны Binance/MEXC (в песочнице биржевые
API закрыты). Скрипт использует только стандартную библиотеку.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

VISION_BASE = "https://data.binance.vision/data/spot/monthly/klines"
MEXC_BASE = "https://api.mexc.com/api/v3/klines"

TIMEFRAMES = {"1h", "4h", "1d", "1m", "5m", "15m", "30m", "2h", "6h", "8h", "12h", "1w", "1M"}

HEADER = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"

DEFAULT_RETRIES = 4
DEFAULT_BACKOFF = 1.5


def _urlopen_with_retry(url: str, timeout: int = 60, retries: int = DEFAULT_RETRIES, backoff: float = DEFAULT_BACKOFF):
    """Скачать URL с ретраями при сетевых ошибках и 5xx/429."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "astra-bot/1.1"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_exc = exc
            wait = backoff ** attempt
            print(f"    сетевой сбой (попытка {attempt+1}/{retries}): {exc!r}; пауза {wait:.1f}с", file=sys.stderr)
            time.sleep(wait)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504):
                wait = backoff ** attempt
                print(f"    HTTP {exc.code} (попытка {attempt+1}/{retries}); пауза {wait:.1f}с", file=sys.stderr)
                time.sleep(wait)
                last_exc = exc
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("unreachable")


def vision_monthly_url(symbol: str, timeframe: str, year: int, month: int) -> str:
    """URL помесячного архива Binance Vision."""
    return f"{VISION_BASE}/{symbol.upper()}/{timeframe}/{symbol.upper()}-{timeframe}-{year:04d}-{month:02d}.zip"


def iter_months(start: datetime, end: datetime):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def parse_klines_csv(text: str) -> list[str]:
    """Оставить только строки klines (каждая начинается с числа)."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.split(",", 1)[0].isdigit():
            out.append(line)
    return out


def fetch_binance_vision(symbol: str, timeframe: str, start: datetime, end: datetime) -> tuple[str, dict]:
    """Скачать и склеить помесячные архивы Vision. Возвращает (CSV-текст, мета)."""
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    rows: list[str] = []
    months_attempted = 0
    months_ok = 0
    months_failed = 0
    failures: list[str] = []
    months = list(iter_months(start, end))
    for idx, (y, m) in enumerate(months):
        months_attempted += 1
        url = vision_monthly_url(symbol, timeframe, y, m)
        try:
            raw = _urlopen_with_retry(url, timeout=60)
        except Exception as exc:
            months_failed += 1
            failures.append(f"{y:04d}-{m:02d}:{exc.__class__.__name__}")
            print(f"  {y:04d}-{m:02d}: нет данных ({exc})", file=sys.stderr)
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                name = zf.namelist()[0]
                text = zf.read(name).decode("utf-8")
        except Exception as exc:
            months_failed += 1
            failures.append(f"{y:04d}-{m:02d}:zip:{exc!r}")
            print(f"  {y:04d}-{m:02d}: битый архив ({exc})", file=sys.stderr)
            continue
        lines = parse_klines_csv(text)
        lines = [
            line
            for line in lines
            if start_ms <= int(line.split(",", 1)[0]) < end_ms
        ]
        rows.extend(lines)
        months_ok += 1
        print(f"  {y:04d}-{m:02d}: {len(lines)} свечей", file=sys.stderr)
        if idx < len(months) - 1:
            time.sleep(0.2)
    csv = HEADER + "\n".join(sorted(set(rows))) + "\n" if rows else ""
    meta = {
        "source": "binance_vision",
        "months_attempted": months_attempted,
        "months_ok": months_ok,
        "months_failed": months_failed,
        "failures_sample": failures[:5],
        "candles": len(rows),
    }
    return csv, meta


def fetch_mexc(symbol: str, timeframe: str, start: datetime, end: datetime) -> tuple[str, dict]:
    """Fallback: MEXC REST, пагинация по 1000 свечей с ретраями."""
    interval = {"1h": "60m", "4h": "240m", "1d": "1d"}.get(timeframe, timeframe)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    rows: list[str] = []
    pages = 0
    cursor = start_ms
    while cursor < end_ms:
        url = (
            f"{MEXC_BASE}?symbol={symbol.upper()}&interval={interval}"
            f"&startTime={cursor}&endTime={end_ms}&limit=1000"
        )
        try:
            raw = _urlopen_with_retry(url, timeout=60)
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            print(f"  MEXC сбой на курсоре {cursor}: {exc}", file=sys.stderr)
            break
        pages += 1
        if not data:
            break
        for bar in data:
            ts = int(bar[0])
            # MEXC отдаёт ровно те же 11 полей, что и Vision — используем напрямую,
            # но для совместимости нормализуем count/taker поля (они не приходят в kline-endpoint).
            if len(bar) >= 11:
                rows.append(",".join(str(x) for x in bar[:11]) + ",0")
            else:
                rows.append(f"{ts},{bar[1]},{bar[2]},{bar[3]},{bar[4]},{bar[5]},{bar[6]},0,0,0,0,0")
        last_ts = int(data[-1][0])
        cursor = last_ts + 1
        if len(data) < 1000:
            break
        time.sleep(0.25)
        if pages % 20 == 0:
            print(f"  до {datetime.fromtimestamp(last_ts / 1000, tz=UTC).date()}: {len(rows)} свечей ({pages} страниц)", file=sys.stderr)
    csv = HEADER + "\n".join(sorted(set(rows))) + "\n" if rows else ""
    meta = {"source": "mexc", "pages": pages, "candles": len(rows)}
    return csv, meta


def main() -> int:
    parser = argparse.ArgumentParser(description="Выгрузка klines Binance Vision / MEXC")
    parser.add_argument("--symbol", default=None, help="Одиночный символ")
    parser.add_argument("--symbols", default=None, help="Список символов через запятую")
    parser.add_argument("--timeframe", default=None, help="Одиночный таймфрейм")
    parser.add_argument("--timeframes", default=None, help="Список таймфреймов через запятую")
    parser.add_argument("--start", default="2021-01-01", help="YYYY-MM-DD")
    parser.add_argument("--end", default="2026-08-22", help="YYYY-MM-DD")
    parser.add_argument("--out", default=None, help="выходной CSV (для одиночного файла)")
    parser.add_argument("--data-dir", default="data", help="Каталог выгрузки")
    parser.add_argument("--source", default="binance", choices=["binance", "mexc"])
    args = parser.parse_args()

    symbols_raw = args.symbols or args.symbol or "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT,LTCUSDT"
    timeframes_raw = args.timeframes or args.timeframe or "1h,4h,1d"

    symbols = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()]
    timeframes = [tf.strip() for tf in timeframes_raw.split(",") if tf.strip()]

    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)

    data_dir = Path(__file__).resolve().parent.parent / args.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    coverage: dict[str, dict] = {}

    for sym in symbols:
        for tf in timeframes:
            if args.out and len(symbols) == 1 and len(timeframes) == 1:
                out = Path(args.out)
            else:
                out = data_dir / f"{sym}_{tf}.csv"
            out.parent.mkdir(parents=True, exist_ok=True)

            print(f"→ {sym} {tf} {start.date()} → {end.date()} (primary={args.source})", file=sys.stderr)
            used_source = args.source
            csv_text = ""
            meta: dict = {}
            try:
                if args.source == "binance":
                    csv_text, meta = fetch_binance_vision(sym, tf, start, end)
                else:
                    csv_text, meta = fetch_mexc(sym, tf, start, end)
            except Exception as exc:
                print(f"  ⚠️ первичный источник упал: {exc}", file=sys.stderr)
                csv_text, meta = "", {"primary_error": repr(exc)}

            if not csv_text:
                fallback = "mexc" if args.source == "binance" else "binance"
                print(f"  ⚠️ первичный источник не дал свечей, пробую fallback={fallback}", file=sys.stderr)
                try:
                    if fallback == "binance":
                        csv_text, fb_meta = fetch_binance_vision(sym, tf, start, end)
                    else:
                        csv_text, fb_meta = fetch_mexc(sym, tf, start, end)
                    used_source = f"{args.source}+{fallback}"
                    meta["fallback"] = fb_meta
                except Exception as exc:
                    print(f"  ⚠️ fallback тоже упал: {exc}", file=sys.stderr)
                    meta["fallback_error"] = repr(exc)

            if not csv_text:
                print(f"❌ {sym} {tf}: ни один источник не дал данных", file=sys.stderr)
                coverage[f"{sym}_{tf}"] = {
                    "symbol": sym, "timeframe": tf, "source": used_source,
                    "candles": 0, "first_ts": None, "last_ts": None,
                    "note": "no data from any source", **meta,
                }
                continue

            out.write_text(csv_text, encoding="utf-8")

            # coverage metadata
            lines = [ln for ln in csv_text.splitlines() if ln and ln.split(",", 1)[0].isdigit()]
            first_ts = int(lines[0].split(",", 1)[0]) if lines else None
            last_ts = int(lines[-1].split(",", 1)[0]) if lines else None
            n = len(lines)
            coverage[f"{sym}_{tf}"] = {
                "symbol": sym,
                "timeframe": tf,
                "source": used_source,
                "candles": n,
                "first_ts": first_ts,
                "first_ts_iso": datetime.fromtimestamp(first_ts/1000, tz=UTC).isoformat() if first_ts else None,
                "last_ts": last_ts,
                "last_ts_iso": datetime.fromtimestamp(last_ts/1000, tz=UTC).isoformat() if last_ts else None,
                "file": str(out.relative_to(data_dir.parent)),
                **meta,
            }
            print(f"✅ {sym} {tf}: {n} свечей → {out} (источник={used_source})")

    (data_dir / "coverage.json").write_text(
        json.dumps({
            "generated_at": datetime.now(UTC).isoformat(),
            "requested_start": start.isoformat(),
            "requested_end": end.isoformat(),
            "requested_end_ms": int(end.timestamp()*1000),
            "series": coverage,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nCoverage манифест: {data_dir / 'coverage.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
