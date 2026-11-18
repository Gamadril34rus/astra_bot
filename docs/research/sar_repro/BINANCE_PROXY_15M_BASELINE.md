# Parabolic SAR 15m: Binance USD-M proxy baseline

**Status: rejected as a standalone trading strategy on the tested sample.** This is a research result only. No ASTRA BOT runtime settings or trading logic were changed.

## Dataset

- Source: Binance USD-M perpetual futures. This is a cross-exchange proxy, not BingX execution-price history.
- Period: 2025-11-01 00:00 UTC through 2026-11-01 00:00 UTC, end exclusive.
- Symbols: BTCUSDT, ETHUSDT, SOLUSDT.
- Interval: 15m.
- Per symbol: 35,040 candles, 0 missing bars, 0 duplicates, 1,095 funding events.
- Funding mark joins: 1,092 exact 1m mark-price bucket opens per symbol; 1 event uses the previous 1m mark close; 2 events use the previous 15m trade-kline close as an explicitly labelled mark-price proxy. No funding event is left without a price, but those 3 fallback joins per symbol are approximate.

The collector verifies source archive SHA-256 checksums and writes a manifest with input and output hashes. The frozen dataset and replay outputs are attached to [GitHub Actions run #24](https://github.com/Gamadril34rus/astra_bot/actions/runs/44345994963), artifact `sar-binance-proxy-15m-2025-11-01-to-2026-11-01` (14-day retention).

## Rules reproduced

- Parabolic SAR: Start 0.02, Increment 0.02, Maximum 0.2.
- Signal: act after a completed candle flips SAR direction.
- Entry: next candle open.
- Initial and trailing stop: SAR value from the previous completed candle.
- Stop fill: candle open on a gap through the stop; otherwise the SAR stop price.
- Funding: historical Binance settlement rate and joined mark price; settlement applies strictly between entry and the conservative exit cutoff.
- Warm-up: 50 bars.
- No parameter search or optimization.

The video illustrates XAUUSD. This test evaluates the same described rules on BTC, ETH and SOL perpetuals, not on gold.

## Baseline result

Assumed execution costs: taker fee 0.05% per side and slippage 0.10% per side. These are explicit research assumptions, not a claim about the user's actual BingX fee tier or realized slippage.

| Metric | Result |
|---|---:|
| Trades | 8,423 |
| Win rate | 23.54% |
| Net profit factor | **0.444** |
| Average net trade | **-30.28 bps** |
| Median net trade | -49.63 bps |
| Median holding time | 165 min |
| Mean holding time | 186.5 min |
| 90th percentile holding time | 345 min |
| Longest holding time | 17 h |
| Profitable calendar months | **0 of 12** |

Funding contribution averaged -0.012 bps per trade. Funding is not the main reason for the negative result.

## Cost sensitivity

| Scenario | Fee per side | Slippage per side | Net PF | Avg net trade |
|---|---:|---:|---:|---:|
| No fees / no slippage | 0.00% | 0.00% | 0.991 | -0.28 bps |
| Low fee, no slippage | 0.02% | 0.00% | 0.880 | -4.28 bps |
| 0.05% fee, no slippage | 0.05% | 0.00% | 0.742 | -10.28 bps |
| Baseline | 0.05% | 0.10% | 0.444 | -30.28 bps |
| 1.5x baseline costs | 0.075% | 0.15% | 0.316 | -45.28 bps |

Even with zero fees and zero slippage, the result is approximately flat but slightly negative. The strategy does not show a positive pre-cost edge on this sample.

## Baseline by symbol and direction

| Symbol | Side | Trades | Net PF | Avg net trade |
|---|---|---:|---:|---:|
| BTCUSDT | Long | 1,419 | 0.333 | -31.30 bps |
| BTCUSDT | Short | 1,418 | 0.355 | -30.29 bps |
| ETHUSDT | Long | 1,359 | 0.470 | -29.33 bps |
| ETHUSDT | Short | 1,352 | 0.518 | -26.80 bps |
| SOLUSDT | Long | 1,438 | 0.463 | -32.91 bps |
| SOLUSDT | Short | 1,437 | 0.492 | -30.83 bps |

No symbol or direction is profitable under the baseline assumptions.

## Chronological validation

No parameters were tuned on these periods. The year was split chronologically 60% / 20% / 20%.

| Segment | Period | Trades | Net PF | Avg net trade |
|---|---|---:|---:|---:|
| In-sample 60% | 2025-11-01 to 2026-06-08 | 5,057 | 0.471 | -30.98 bps |
| Validation 20% | 2026-06-08 to 2026-08-20 | 1,657 | 0.420 | -27.44 bps |
| Out-of-sample 20% | 2026-08-20 to 2026-11-01 | 1,709 | **0.372** | **-30.98 bps** |

The out-of-sample segment is materially negative. The reverse initial SAR seed produced the same baseline statistics after the 50-bar warm-up.

## Interpretation and decision

1. The exact described 15m SAR rules do not meet the minimum research gate: net PF is below 1, expectancy is negative, and none of the 12 months is profitable.
2. Costs worsen the result, but do not create the failure: the zero-cost PF is still only 0.991.
3. Median holding time is 2 h 45 min, but the longest position lasted 17 h. A 15m chart does not mean every trade is a short-duration trade.
4. Do not promote this standalone SAR setup to VST or the demo trading loop. Do not choose leverage or margin from a negative expectancy result.
5. This does not prove every possible SAR use is unprofitable. It rejects this unfiltered, standalone interpretation on this one-year Binance proxy sample. Any later filter or hybrid is a new hypothesis and must be tested on untouched data.
6. No production code, REAL/VST routing, risk settings, leverage or activation flags were changed.
