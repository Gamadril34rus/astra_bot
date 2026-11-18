# Fixed regime-filter experiments for Parabolic SAR 15m

**Decision: no tested filter rescues the standalone SAR strategy.** Research only; no production or demo trading changes.

## Experimental design

The experiment uses the same frozen one-year Binance USD-M perpetual proxy sample as [the baseline report](BINANCE_PROXY_15M_BASELINE.md): BTCUSDT, ETHUSDT and SOLUSDT, 15m candles, 2025-11-01 through 2026-11-01 UTC. This is not BingX execution history.

The SAR rules, entry/stop logic, funding treatment and baseline costs are unchanged. Costs are assumed at 0.05% taker fee and 0.10% slippage per side.

Only three fixed hypotheses were tested. No parameter grid or threshold optimization was performed:

1. **15m ADX(14) >= 20**, measured on the completed signal candle.
2. **1h EMA(200) direction alignment**: latest fully closed 1h candle must close above EMA200 for a long, below EMA200 for a short.
3. **Both filters together.**

The 1h timestamp is explicitly converted to epoch milliseconds. The runner rejects any matched hourly candle whose close timestamp is later than the trade entry timestamp. The first exploratory run with ambiguous timestamp units was discarded; only the corrected, guarded run below is valid.

## Results

| Variant | Trades total | Overall net PF | Overall avg net | OOS trades | OOS net PF | OOS avg net |
|---|---:|---:|---:|---:|---:|---:|
| Unfiltered baseline | 8,423 | 0.444 | -30.28 bps | 1,709 | 0.372 | -30.98 bps |
| ADX(14) >= 20 | 5,355 | 0.452 | -30.53 bps | 1,012 | 0.411 | -29.63 bps |
| 1h EMA200 alignment | 3,868 | 0.444 | -30.54 bps | 812 | 0.373 | -30.44 bps |
| Both filters | 2,374 | 0.456 | -30.59 bps | 467 | 0.400 | -29.04 bps |

### Chronological split

| Variant | IS PF | Validation PF | OOS PF |
|---|---:|---:|---:|
| Unfiltered baseline | 0.471 | 0.420 | 0.372 |
| ADX(14) >= 20 | 0.474 | 0.409 | 0.411 |
| 1h EMA200 alignment | 0.472 | 0.418 | 0.373 |
| Both filters | 0.494 | 0.372 | 0.400 |

### OOS sample size by direction

| Variant | Long | Short |
|---|---:|---:|
| Unfiltered baseline | 857 | 852 |
| ADX(14) >= 20 | 508 | 504 |
| 1h EMA200 alignment | 451 | 361 |
| Both filters | 263 | 204 |

All variants exceed the minimum directional sample count, but none comes close to positive expectancy. All four variants recorded **0 profitable months out of 12**.

## Interpretation

- ADX(14) >= 20 gives a small OOS PF improvement from 0.372 to 0.411, but average trade remains -29.63 bps.
- The 1h EMA200 filter is effectively no better than baseline OOS: PF 0.373.
- Combining both filters raises OOS PF to 0.400, still deeply negative, with -29.04 bps per trade.
- The filters reduce trade count; they do not create a durable edge.
- No candidate meets even PF 1.0, let alone the ASTRA research gate of net PF >= 1.25 with positive OOS expectancy.

**Stop this standalone SAR branch of research here.** Do not optimize thresholds, select leverage, or promote any variant to VST/demo. A materially different entry/exit concept would be a new strategy hypothesis, not a parameter tweak to this failed setup.

## Reproduction

The fixed-filter runner is `sar_proxy_filter_experiments.py`. The workflow independently downloads and SHA-256-validates the Binance USD-M archives, applies the filters, and uploads the result files.

Corrected run: [GitHub Actions run #3](https://github.com/Gamadril34rus/astra_bot/actions/runs/44348619453), artifact `sar-proxy-fixed-filters-2025-11-01-to-2026-11-01` (14-day retention).

No runtime code, REAL/VST routing, risk limits, leverage or activation settings were changed.
