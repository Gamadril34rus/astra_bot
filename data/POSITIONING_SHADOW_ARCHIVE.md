# Research-only positioning shadow archive

This branch stores read-only public market data for the MOEX-inspired positioning-pressure experiment.

## Data layout

- `data/bingx_usdm/*_30m.csv`: BingX USDT-M 30m klines, including OHLCV, trade count, taker-buy base volume and taker-buy quote volume when returned by BingX.
- `data/bingx_usdm/open_interest/*_open_interest.csv`: timestamped public total open-interest snapshots for BTC, ETH, SOL, HYPE and INJ.
- Manifests record coverage, source timestamps and checksums.

## Collection contract

- Prospective OI collection starts only after explicit owner approval and manual initialization of the workflow.
- The scheduled collector runs every 30 minutes, aligned to the bot's signal timeframe.
- Missing fields remain empty/unknown; no zero imputation.
- Duplicate source timestamps must agree; candle OHLCV is immutable.
- Collection code and analysis are research-only. This branch is a data archive, not a production configuration branch.

## Critical interpretation limits

BingX exposes total open interest and aggressive buy volume, not MOEX's physical-person/legal-entity split. OI and taker-side volume cannot identify who opened or closed a position. Any directional interpretation is a heuristic and must be tested prospectively against forward returns before any strategy proposal.
