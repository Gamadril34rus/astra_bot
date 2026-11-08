# Binance USDⓈ-M historical candles (research data)

**Venue:** Binance USDⓈ-M Futures public archive  
**Source:** https://data.binance.vision/?prefix=data/futures/um/monthly/klines/  
Daily fill for incomplete months: `data/futures/um/daily/klines/`

**Not BingX.** These OHLCV series are **not** demo/live BingX executions, bid/ask quotes, or funding cash flows. Do not treat them as fill tape for ASTRA shadow portfolios without an explicit execution model.

## Layout

```
binance_usdm/{timeframe}/{symbol}/{YYYY-MM}.csv.gz
```

Timeframes: `15m`, `30m`, `1h`, `4h`  
Symbols: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `HYPEUSDT`, `INJUSDT`

Each symbol starts at its own first available Binance archive month (no backfill from another contract).

## CSV columns

| column | meaning |
|--------|---------|
| open_time_utc | candle open, ISO-8601 UTC |
| open_time_ms | open time, unix ms |
| open, high, low, close | prices as published |
| volume | base asset volume |
| quote_volume | quote asset volume |
| trades | number of trades |

Gzip-compressed CSV with header. Number strings keep source precision.

## Forming candles

Only closed candles are stored. For the current partial month (daily archives), a bar is dropped if `open_time + timeframe > collection_time`.

## Integrity

- Monthly ZIP files verified against published `.CHECKSUM` (SHA256) when downloaded.
- `manifest.json` lists every file with `sha256`, row count, first/last open time, and source (`binance_vision_monthly_zip` or `binance_vision_daily_zip`).
- Gaps are **not** interpolated. See `quality_summary.json` / `manifest.json` → `series[].gap_count`.

## Known coverage notes

| Symbol | Approx. first open (UTC) | Notes |
|--------|--------------------------|-------|
| BTCUSDT | 2020-01-01 | Full monthly set through 2026-10 + daily Nov 2026 |
| ETHUSDT | 2020-01-01 | Same |
| SOLUSDT | 2020-09-14 | 2026-11-07 daily appended after initial collection |
| HYPEUSDT | 2025-05-30 | Futures listing day |
| INJUSDT | 2022-08-17 | Futures listing day |

Most series show a single structural gap (typical around early listing / archive seams). SOL has additional historical gaps.

## How to read

```python
import gzip, csv
from pathlib import Path

path = Path("binance_usdm/4h/BTCUSDT/2024-06.csv.gz")
with gzip.open(path, "rt", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        print(row["open_time_utc"], row["close"])
```

See `samples/` for short plain-text heads without downloading the full tree.

## Size

About **53–55 MB** compressed under `binance_usdm/` (under the 100 MB soft limit). Raw ZIP cache is **not** part of the git tree.

## Transfer note (2026-11-08)

Full `binance_usdm/` tree was built and verified locally (orphan commit `7c9a2ad`). Bulk binary push via GitHub connector is impractical for 1248 gzip files; metadata and samples are committed here. Full tree can be restored from local artifact or by re-running the public download from data.binance.vision using the plan in the research scripts.
