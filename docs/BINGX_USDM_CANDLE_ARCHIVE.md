# BingX USDT-M candle archive

This archive uses the public BingX swap v3 kline endpoint through the repository's
BingXClient. It is perpetual-futures OHLCV, not Binance spot candles.

## Scope

- Symbols: BTC-USDT, ETH-USDT, SOL-USDT, HYPE-USDT, INJ-USDT.
- Default timeframes: 5m, 15m, 1h, 4h, 1d. These match the active Zeus decision
  path: 5m micro-confirmation, 15m entry, 1h direction, 4h structure, 1d context.
- 1m is supported by the downloader as an explicit workflow-dispatch override,
  but is not in the default archive: it appears in the generic project candle
  config, not the active Zeus decision timeframe tuple, and multiplies history
  size/API cost substantially. 3m and other exchange intervals are not in the
  active strategy config.
- Initial history request starts at 2021-01-01 UTC. The actual first bar is the
  earliest bar BingX returns for each contract.
- Only closed candles are written. The current still-open candle is excluded.
- CSV rows are keyed by candle open_time (milliseconds UTC) and kept in ascending
  order. Existing values are immutable; a changed value for an existing timestamp
  is a hard error, not a silent overwrite.
- The manifest records actual coverage, last closed bar, gaps, and API page counts.

## Operation

Workflow: .github/workflows/bingx-usdm-candle-archive.yml

1. The first scheduled run automatically detects a missing manifest and performs
   the initial historical backfill. No backfill runs on PR merge itself.
2. After manifest.json exists, the daily schedule appends bars newer than the last
   archived timestamp using the timeframe set recorded in the manifest. The
   initial default is 5m/15m/1h/4h/1d; a manual initial dispatch can explicitly
   add 1m. Later daily runs inherit that chosen set unless explicitly overridden.
3. A manual workflow_dispatch can still select mode=initial or mode=daily.
3. If the manifest reports gaps or a data conflict, investigate and run a
   controlled initial/backfill repair separately. Daily mode does not rewrite
   old bars or hide missing intervals.

The workflow uses only public market-data requests. It requires no API key and
has no trading, account, order, or position permissions.
