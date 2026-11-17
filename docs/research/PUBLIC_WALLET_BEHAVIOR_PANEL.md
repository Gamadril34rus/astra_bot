# Public wallet behaviour panel — first executable collection pass

**Status:** research-only, read-only, isolated from ASTRA runtime. No trading, strategy registration, VST/REAL, risk, leverage, or exit-engine changes.

## What this pass does

The collector snapshots the public Hyperliquid leaderboard and forms a stratified cohort from five dimensions: monthly PnL, weekly PnL, monthly traded volume, account value, and low monthly ROI. The low-ROI stratum is intentional: studying only winners would bake survivorship bias into the sample. It then retrieves the latest available wallet fills using `userFillsByTime`, stores each raw response with a SHA-256 hash, and reports open/close/add classifications, taker share, fees and reported realised PnL.

Run:

```bash
python scripts/public_wallet_behavior_research.py --limit 100 --pause-seconds 0.35
```

The GitHub Actions workflow `Public wallet behaviour research (read-only)` runs on pushes to this research branch and uploads the raw panel and manifest as an immutable artifact.

## Hard limitations

1. This is a **prospective cohort snapshot**, not a historical leaderboard reconstructed at old dates. It cannot eliminate historical survivorship bias. The cohort must be frozen now and followed forward across repeated snapshots.
2. Hyperliquid documents a maximum of 2,000 fills per response and only the latest 10,000 fills per wallet through `userFillsByTime`. Wallets at the cap are explicitly flagged; older fills are not inferred.
3. TWAP slice fills are not included in this first pass and require separate endpoints.
4. Fill fees and `closedPnl` are not full account profitability. Funding, deposits, withdrawals, transfers and other ledger events must be reconciled before ranking a wallet by net performance.
5. Wallet addresses are pseudonymous accounts. They are not verified hedge funds, institutions or individual legal persons.
6. Hyperliquid is only a behavioural hypothesis source. It does not supply BingX executions, BingX slippage or BingX price paths.

## Interpretation

The first pass is a data acquisition and provenance gate, not a strategy result. The next analysis should reconstruct position episodes from ordered fills, add TWAP slices and funding/ledger events, calculate holding-time and scale-in/scale-out distributions, and test repeated entry/exit context against closed 15m/1h bars. Only then should behavioural features be translated into separate BingX-native modules.

BingX remains the execution venue for final validation. The existing native BingX archive is currently only about 100 common days, so no untouched OOS result can be claimed until the history gap is resolved. OKX downloadable historical tick data can be used as a secondary market-microstructure robustness study, but not as a replacement for BingX prices, fills, funding or costs.
