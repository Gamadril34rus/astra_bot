# 4h breakout-retest: walk-forward audit

<badge>INVALIDATED</badge> **Do not use the historical positive metrics or these archived CSVs to justify a live or paper promotion.**

## What was claimed

The original report, committed on 2026-09-15, stated that the fixed `break` configuration (RR=1.5, volume ratio >=1.3, ATR x1.5) had OOS PF 1.238 on 81 trades, with positive mean R. That result is not reproduced by the original backtest implementation on the archived input files.

## Reproduction of the original Python script

A GitHub Actions workflow installed pinned NumPy/Pandas versions, fetched the exact data revision `307d684174bddc0c32c22929ab5d7411fa576d6d`, checked the SHA-256 hashes, and ran the original `scripts/research_bt_walkforward.py` unchanged.

- [Reproduction workflow run](https://github.com/Gamadril34rus/astra_bot/actions/runs/42593168499)
- [Pinned data branch commit](https://github.com/Gamadril34rus/astra_bot/commit/307d684174bddc0c32c22929ab5d7411fa576d6d)
- Original script commit: `8f3c2121c85f2ca112cd9ac9cf7620c69402a08d`
- Original script blob on master: `2d84d556a517b422a613e9e009b9afd3a075d66e`

The original script produced these results on the archived files:

| Configuration | Period | Trades | PF | Mean R | Win rate | Net at 0.5% risk | Max drawdown |
|---|---|---:|---:|---:|---:|---:|---:|
| break, RR 1.5, volume >=1.3, ATR x1.5 | IS 2021–2023 | 208 | 0.784 | -0.145 | 38.5% | -14.3% | 17.2% |
| same | OOS 2024–2026 | 196 | 0.789 | -0.140 | 38.8% | -13.2% | 13.7% |
| same | Full 2021–2026 | 413 | 0.779 | -0.148 | 38.5% | -26.9% | 29.9% |

Other configurations were also negative on OOS:

| Configuration | OOS trades | OOS PF | OOS mean R | Net at 0.5% risk |
|---|---:|---:|---:|---:|
| break, RR 2.0, volume >=1.3, ATR x1.5 | 189 | 0.854 | -0.103 | -9.6% |
| break, RR 1.5, volume >=1.5, ATR x1.5 | 140 | 0.837 | -0.107 | -7.5% |
| ema, RR 2.0, volume >=1.8, ATR x1.5 | 331 | 0.896 | -0.073 | -12.0% |
| ema, RR 2.0, volume >=2.0, ATR x1.5 | 167 | 0.840 | -0.113 | -9.4% |

The script's own `BEST OOS CONFIG` section was empty: no tested configuration met its positive-PF and positive-mean-R selection condition.

**Important:** these are reproducibility results on the archived files, not valid market-performance estimates. The input data fail symbol/date price-scale checks, as described below. The negative results must not be interpreted as a verified real-market backtest either.

## Data provenance failure

The files are continuous 4h OHLCV series, but their prices do not match the named markets and dates. Examples from the archived data:
- The BTC file records an open around 12,939.98 USDT on 2025-01-01 00:00 UTC, far from the public BTC market level near 93,425 USDT.
- The SOL file records about 5.16 USDT on 2024-01-01 and about 1.58 USDT on 2025-01-01, inconsistent with the named SOL market.

Archived blob IDs and SHA-256:
| File | Git blob SHA | SHA-256 |
|---|---|---|
| BTCUSDT_4h.csv | `bf1119238982838f2ef3fd9323bc30275ac30a38` | `5406cfe0c6aa787677e370a58803f1ce0c9c9ce9c5202c86fd368f6b91b35ec0` |
| ETHUSDT_4h.csv | `8eb0a7ee2137f9da2e787e228920447c08818587` | `8fc1ad6ecf0ab580b727938652b58923327eefd219162b06ff58b4db3dc96df6` |
| SOLUSDT_4h.csv | `5375a7af1298cf7c6ac88fc310a14bbb20b878f9` | `b51e26abfe383f10094afec5cef87c69a565f423867c54d1f10608b1d5322e37` |

The positive metrics in the previous version are withdrawn. Replacing the data and rerunning a frozen, independently reviewed test is required before any performance claim is restored.

## Promotion decision

- Do not enable the research candidate in live or Zeus paper.
- Do not use the archived PF, win rate, mean R, or drawdown for sizing, ranking, or configuration decisions.
- Replace the data with source-verified exchange klines and record the venue, contract type, symbol, timeframe, download time, endpoint, and checksum.
- Revalidate symbol price scale, bar spacing, duplicates, gaps, OHLC constraints, and indicator warm-up.
- Re-run the full execution model, including next-bar entry, stop/target precedence, 24-bar time exit, round-trip costs, and one-position-per-symbol.
- Require a fresh, frozen OOS period and positive net expectancy after costs before reconsidering promotion.

*Research only. No live trading conclusion or profitability claim is supported by this dataset.*
