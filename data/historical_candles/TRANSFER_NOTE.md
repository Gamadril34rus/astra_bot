# Transfer status 2026-11-08

- Branch created from master: data/binance-usdm-historical-candles-20261108
- Full candle tree (1248 × .csv.gz, ~55.4 MB) verified locally and kept in artifact:
  /home/workdir/artifacts/astra_candles_pr_clean/data/historical_candles/binance_usdm
  and orphan local commit 7c9a2adb (do not delete).
- GitHub connector `push_files` accepts string content; impractical for 55 MB binary tree in one/many calls (payload limits, binary handling).
- Metadata (README, quality_summary, samples) pushed via connector.
- To complete the tree in this branch: an executor with GitHub write access (or local git + token) can copy the verified tree into the branch and push. Reproducible alternative: re-download from data.binance.vision using the original monthly+daily plan (CHECKSUM-verified).
- SOLUSDT 2026-11-07 daily was missing at initial collection (08:12 UTC) and was appended after it became available (~09:00 UTC publication). All other symbols already had closed 07 Nov candles.
- No trading code, workflows, state, HALT or secrets included.
