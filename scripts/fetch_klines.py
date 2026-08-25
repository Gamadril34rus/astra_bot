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
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

VISION_BASE = "https://data.binance.vision/data/spot/monthly/klines"
MEXC_BASE = "https://api.mexc.com/api/v3/klines"

TIMEFRAMES = {"1h", "4h", "1d", "1m", "5m", "15m", "30m", "2h", "6h", "8h", "12h", "1w", "1M"}

HEADER = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"


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


def fetch_binance_vision(symbol: str, timeframe: str, start: datetime, end: datetime) -> str:
    """Скачать и склеить помесячные архивы Vision. Возвращает CSV-текст."""
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    rows: list[str] = []
    months = list(iter_months(start, end))
    for idx, (y, m) in enumerate(months):
        url = vision_monthly_url(symbol, timeframe, y, m)
        req = urllib.request.Request(url, headers={"User-Agent": "astra-bot/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
        except Exception as exc:  # месяц может не существовать (новый листинг)
            print(f"  {y:04d}-{m:02d}: нет данных ({exc})", file=sys.stderr)
            continue
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            name = zf.namelist()[0]
            text = zf.read(name).decode("utf-8")
        lines = parse_klines_csv(text)
        lines = [
            line
            for line in lines
            if start_ms <= int(line.split(",", 1)[0]) < end_ms
        ]
        rows.extend(lines)
        print(f"  {y:04d}-{m:02d}: {len(lines)} свечей", file=sys.stderr)
        if idx < len(months) - 1:
            time.sleep(0.2)
    return HEADER + "\n".join(sorted(set(rows))) + "\n"


def fetch_mexc(symbol: str, timeframe: str, start: datetime, end: datetime) -> str:
    """Fallback: MEXC REST, пагинация по 1000 свечей."""
    interval = {"1h": "60m", "4h": "240m", "1d": "1d"}.get(timeframe, timeframe)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    rows: list[str] = []
    cursor = start_ms
    while cursor < end_ms:
        url = (
            f"{MEXC_BASE}?symbol={symbol.upper()}&interval={interval}"
            f"&startTime={cursor}&endTime={end_ms}&limit=1000"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "astra-bot/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data:
            break
        for bar in data:
            ts = int(bar[0])
            rows.append(
                f"{ts},{bar[1]},{bar[2]},{bar[3]},{bar[4]},{bar[5]},{bar[6]},{bar[7]},0,0,0,0"
            )
        cursor = int(data[-1][0]) + 1
        if len(data) < 1000:
            break
        time.sleep(0.3)
        print(f"  до {datetime.fromtimestamp(cursor / 1000, tz=UTC).date()}: {len(rows)} свечей", file=sys.stderr)
    return HEADER + "\n".join(sorted(set(rows))) + "\n"


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

    symbols_raw = args.symbols or args.symbol or "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT"
    timeframes_raw = args.timeframes or args.timeframe or "1h,4h,1d"

    symbols = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()]
    timeframes = [tf.strip() for tf in timeframes_raw.split(",") if tf.strip()]

    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)

    data_dir = Path(__file__).resolve().parent.parent / args.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    for sym in symbols:
        for tf in timeframes:
            if args.out and len(symbols) == 1 and len(timeframes) == 1:
                out = Path(args.out)
            else:
                out = data_dir / f"{sym}_{tf}.csv"
            out.parent.mkdir(parents=True, exist_ok=True)

            print(f"Выгрузка {sym} {tf} {start.date()} → {end.date()} ({args.source})", file=sys.stderr)
            if args.source == "binance":
                csv_text = fetch_binance_vision(sym, tf, start, end)
            else:
                csv_text = fetch_mexc(sym, tf, start, end)

            out.write_text(csv_text, encoding="utf-8")
            n = max(len(csv_text.splitlines()) - 1, 0)
            print(f"OK: {n} свечей → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
