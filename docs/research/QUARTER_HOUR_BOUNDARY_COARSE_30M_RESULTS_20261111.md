# Quarter-hour boundary research: coarse 30m proxy results

**Run:** [GitHub Actions #42983106181](https://github.com/Gamadril34rus/astra_bot/actions/runs/42983106181)  
**Artifact:** `quarter-hour-boundary-research` (raw 30m CSVs, funding CSVs and machine-readable report)  
**Code SHA:** `c3504fcef72e556758dfebe156b65926c6b9a3e5`  
**Status:** `COARSE_PROXY_ONLY_NOT_THE_15M_OR_5M_HYPOTHESIS`

## Verdict

The coarse 30m proxy is **rejected as a research candidate**. Both reversal and continuation have negative OOS mean event returns after base fees and slippage on all four tested horizons. All five symbols are negative for both variants at every OOS horizon. The 1.5x cost stress makes the results worse.

This does **not** falsify the original 5m/15m hypothesis. Historical 5m and 15m candles were unavailable for the frozen IS, Validation and OOS windows, so that exact hypothesis remains untested. The 30m proxy observes only :00 and :30, not :15 and :45, and uses the entire 30m candle return rather than the first 5m/10-second impulse.

## Data feasibility and quality

A BingX public-kline probe returned 100 latest 5m candles but 0 rows at the IS, Validation and OOS sample dates. The same result held for 15m. Historical 30m data was available at all sample dates.

The canonical 30m archives below were verified against the positioning-data manifest. All have zero gaps and zero populated taker-buy-volume rows.

| Symbol | 30m bars | First UTC bar | Last UTC bar | Gaps | taker-buy rows |
|---|---:|---|---|---:|---:|
| BTC-USDT | 23,535 | 2025-07-09 00:00 | 2026-11-11 07:00 | 0 | 0 |
| ETH-USDT | 21,087 | 2025-08-29 00:00 | 2026-11-11 07:00 | 0 | 0 |
| SOL-USDT | 27,775 | 2025-04-11 16:00 | 2026-11-11 07:00 | 0 | 0 |
| HYPE-USDT | 10,575 | 2026-04-05 00:00 | 2026-11-11 07:00 | 0 | 0 |
| INJ-USDT | 20,863 | 2025-09-02 16:00 | 2026-11-11 07:00 | 0 | 0 |

### SHA-256 of source 30m CSVs

| Symbol | SHA-256 |
|---|---|
| BTC-USDT | `962896d2337d0da023646e5d5c996762bfe0cb111df73603e5b5696e395ed8b3` |
| ETH-USDT | `01dae8625dc24f95047d1e1bc0dc0dc5dc7a488eb0a2b54413e9de3f39516c42` |
| SOL-USDT | `fb8b78ce3481d2fa2a295b50e1fae1804b9d54681a2635211acdd94a2f436838` |
| HYPE-USDT | `f3a3ba953133cacfc93e4bcf89ff3959766fc005ff8ec61b944d549bca87d4b9` |
| INJ-USDT | `a6d04e5e9eb4cbf7c47adcbc7774c730e66f0697e95d0ce7abad34e178198272` |

## Frozen coarse-proxy definition

- Signal candle: every completed 30m candle opening at UTC :00 or :30.
- Impulse: absolute open-to-close return from 0.30% to 1.50%.
- Volume: at least 1.5× the median of the previous 20 completed 30m bars.
- Trend: last fully closed 4h candle; bullish if EMA20 > EMA50 and close > EMA20, bearish if EMA20 < EMA50 and close < EMA20. Neutral excluded.
- REVERSAL: fade the impulse when the 4h trend opposes it.
- CONTINUATION: follow the impulse when the 4h trend confirms it.
- Entry proxy: open of the next 30m candle.
- Horizons: 30m, 1h, 2h and 4h.
- Base costs: 12 bps round trip BTC/ETH; 18 bps SOL/HYPE/INJ. Stress: 18 / 27 bps.
- Funding: actual archived settlement rates when coverage brackets the full event interval and no large timestamp gap is detected; otherwise UNKNOWN.
- Split-crossing outcomes are purged. OOS ends 2026-11-10; the partial 11 November UTC day is excluded.

## Pooled OOS results

All returns below are average event returns after base fees and slippage. Events overlap, so these are not portfolio PnL or a strategy PF. The interval is a 95% UTC-day cluster-bootstrap interval for the mean cost-only event return.

| Variant | Horizon | Events | Mean net (bps) | 1.5× costs (bps) | 95% daily-cluster CI (bps) |
|---|---:|---:|---:|---:|---:|
| Reversal | 30m | 1,437 | -11.04 | -18.73 | [-15.80, -6.59] |
| Reversal | 1h | 1,437 | -12.35 | -20.04 | [-19.86, -6.54] |
| Reversal | 2h | 1,437 | -14.49 | -22.18 | [-22.79, -5.95] |
| Reversal | 4h | 1,437 | -13.37 | -21.06 | [-25.88, +0.38] |
| Continuation | 30m | 1,683 | -20.07 | -27.92 | [-26.35, -17.83] |
| Continuation | 1h | 1,683 | -22.19 | -30.03 | [-31.58, -19.67] |
| Continuation | 2h | 1,683 | -23.70 | -31.54 | [-40.45, -24.20] |
| Continuation | 4h | 1,683 | -20.15 | -27.99 | [-48.15, -21.82] |

Funding-adjusted means are also negative on the subset with complete funding coverage. Coverage is incomplete: OOS funding is complete for 890/1,437 reversal outcomes and 1,066/1,683 continuation outcomes. Missing funding was not replaced with zero or a fixed assumed rate.

## OOS per-symbol check

Mean cost-only event return in bps. Each symbol is negative at every horizon for both variants.

| Symbol | Reversal 30m | Reversal 4h | Continuation 30m | Continuation 4h |
|---|---:|---:|---:|---:|
| BTC-USDT | -10.60 | -11.99 | -14.11 | -18.53 |
| ETH-USDT | -11.38 | -13.28 | -19.29 | -25.46 |
| SOL-USDT | -11.02 | -5.14 | -15.12 | -25.71 |
| HYPE-USDT | -9.25 | -5.98 | -23.50 | -14.34 |
| INJ-USDT | -14.22 | -38.50 | -27.77 | -18.99 |

## Interpretation and decision

1. **Reject the coarse 30m proxy.** Neither reversal nor continuation showed positive OOS event expectancy after modeled costs.
2. **Do not port this proxy into Zeus or VST.** It has no demonstrated edge and is not a complete execution backtest.
3. **Do not claim the original 5m/15m hypothesis failed.** Its required historical data was unavailable; the test was not performed.
4. The next valid route for the exact hypothesis is a prospective 5m archive (and preferably trade-level data for the paper's 10-second signed flow). It needs enough untouched history before any OOS claim. No synthetic reconstruction from 30m candles is acceptable.

No production files, `SETTINGS.md`, Zeus strategy, risk settings, VST arming or real order paths were changed by this research.
