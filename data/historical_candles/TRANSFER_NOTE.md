# Transfer status 2026-11-08

- Branch created from **master** (shared history): `data/binance-usdm-historical-candles-20261108`
- Full candle tree (**1248** × `.csv.gz`, **~55.4 MB**) verified locally and kept intact:
  - Path: `/home/workdir/artifacts/astra_candles_pr_clean/data/historical_candles/binance_usdm`
  - Local orphan commit (do not delete): `7c9a2adbd584771e209f9964bb790a1fe504bc72`
- GitHub connector `push_files` / `create_or_update_file` accept string content. Impractical for 55 MB of binary gzip across 1248 files (payload size limits).
- Metadata pushed: README.md, this note, samples (partial), quality_summary.
- To complete the binary tree on this branch: copy the verified local tree into a working clone of this branch and `git add/commit/push`. Or re-download from https://data.binance.vision (CHECKSUM-verified monthly + daily).
- **SOLUSDT 2026-11-07** daily ZIP was not yet published at initial collection (08:12 UTC); it appeared ~09:00 UTC and was appended (closed candles only). All other symbols already had full 07 Nov closed bars.
- Diff contains **only** research candle metadata + samples. No trading code, workflows, state.json, trades.db, HALT logic or secrets.
- PR must **not** be merged until the binary tree is present or an explicit decision to keep only the reproducible download path is made.
