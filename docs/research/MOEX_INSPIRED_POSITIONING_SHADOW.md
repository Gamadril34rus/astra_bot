# MOEX-inspired positioning-pressure shadow for BingX USDT-M

Status: research-only data and descriptive analysis. No strategy entry, exit, sizing, or risk integration.

## 1. What the video shows

The video demonstrates MOEX's intraday futures open-interest product split by client group:
- physical persons (Ф.Лонг / Ф.Шорт),
- legal entities (Ю.Лонг / Ю.Шорт),
- the number of accounts/participants in each direction,
- the change versus the prior close and average contracts per account.

The visible MOEX snapshot is marked 2026-10-29 23:50 MSK, with changes versus 2026-10-28 23:50 MSK. The displayed position counts are:

| Group | Long | Short | Net long-minus-short |
|---|---:|---:|---:|
| Physical persons | 6,445,419 | 1,377,383 | +5,068,036 |
| Legal entities | 12,054,973 | 17,123,009 | -5,068,036 |

The groups' net positions exactly offset. This is expected market clearing, not two independent pieces of evidence. Legal entities are not automatically "smart money"; that bucket may contain dealers, market makers, hedgers and other legal clients. Participant counts and average position size add context but do not reveal motive.

The visible daily changes are:
- Physical long +1,562,427; physical short -999,115.
- Legal long -1,276,507; legal short +1,285,035.
- Number of physical long accounts 15,113 (+6,982); physical short accounts 2,973 (-8,140).
- Number of legal long accounts 261 (+51); legal short accounts 122 (-64).

This is a meaningful data idea. One screenshot and one selected chart segment are not a measured trading edge: the video provides no complete sample, rule definition, fees, funding, out-of-sample results or drawdown.

## 2. What transfers and what does not

MOEX's FUTOI product explicitly separates long/short open positions and the number of participants for individuals and legal entities, and publishes intraday updates. Its published schema includes `POS_LONG`, `POS_SHORT`, `POS_LONG_NUM`, `POS_SHORT_NUM`, client group and publication time. See the MOEX product note and data description:
- https://www.moex.com/n28677
- https://fs.moex.com/f/12828/data-description-en.pdf

BingX's public USDT-M market interface does not expose the same client-group split. Its documented open-interest endpoint returns total open interest in base-asset units, while the kline endpoint includes total volume, trade count, taker-buy base volume and taker-buy quote volume. There is no physical-person/legal-entity field in these public market responses:
- https://github.com/BingX-API/api-ai-skills/blob/main/skills/swap-market/api-reference.md

Therefore this project does **not** claim to reproduce MOEX participant positioning. It captures the nearest observable BingX proxy:
1. Total open interest (OI) change between timestamped public snapshots.
2. Taker-buy share within each completed 30m candle.
3. The price return on that same completed candle.
4. Forward price returns after the observation, for 30m, 1h, 2h, 4h and 9h.

Taker-buy volume identifies the aggressive side of executed volume, not who opened/closed a position. OI is total outstanding exposure, not net long or net short. Every open futures contract has both a long and a short counterparty. Combining OI change with price and taker flow is a market-pressure heuristic, not participant identity or a proof of trade direction.

## 3. Collection contract

### Candle enrichment

The candle parser now preserves BingX v3 kline array fields:
- index 8: number of trades,
- index 9: taker-buy base-asset volume,
- index 10: taker-buy quote-asset volume.

The append-only archive adds optional columns:
- `taker_buy_volume`,
- `taker_buy_quote_volume`.

Older OHLCV rows can be re-fetched and enriched with `--refresh-optional-fields`. Existing OHLCV values remain immutable. Unknown optional values remain blank, not zero. The archive retries BingX 100410 only after a five-minute cooldown, and uses a conservative request pace.

### Open-interest snapshots

`scripts/archive_bingx_open_interest.py` captures the public BingX total-OI endpoint for BTC, ETH, SOL, HYPE and INJ. Each CSV row retains both:
- exchange `source_time`,
- local `observed_at_ms` / `observed_at_utc`.

Snapshots are keyed by local observation time. This preserves repeated observations even if the exchange source timestamp has not advanced. Missing/invalid OI is an error, never zero. CSV writes are atomic; manifests include hashes.

The endpoint is a current snapshot, not historical OI candles. Collection is prospective from the first successful run. It cannot reconstruct the 2025/2026 OI history after the fact.

## 4. Descriptive states, not entries

For every OI observation, the analyzer joins the latest fully completed 30m candle at or before the local observation time. It rejects a snapshot if the matched candle is more than 20 minutes old or the preceding OI observation is more than 90 minutes old.

Features:
- `price_change_pct`: latest completed 30m close versus the preceding 30m close.
- `oi_change_pct`: base-unit OI change versus the prior local observation.
- `taker_buy_share_pct`: taker-buy quote volume / total quote volume where available; otherwise the same ratio in base units.
- `taker_imbalance_pct`: 2 × taker-buy share − 1, expressed as percent.
- `open_interest_usdt_est`: base-unit OI multiplied by the latest closed 30m close; this is a notional estimate, not an exchange-reported USD field.
- Forward close-to-close returns for 30m, 1h, 2h, 4h and 9h after the matched closed candle.

The analyzer buckets observations into eight descriptive combinations:
- price up/down,
- OI up/down,
- taker-buy imbalance positive/negative.

Example interpretation only:
- price up + OI up + buy imbalance positive = price advanced while total exposure expanded and buyers were more aggressive;
- price up + OI down = price advanced while total exposure contracted, which can be consistent with short closing;
- price down + OI down = price fell while total exposure contracted, which can be consistent with long closing;
- price down + OI up + sell imbalance positive = price fell while total exposure expanded and sellers were more aggressive.

These are not unique explanations. The buyer/seller that initiated each trade may be opening or closing, and the OI endpoint does not classify them. The analysis reports conditional forward returns; it does not create a long/short signal.

## 5. Evaluation protocol

1. Preserve the parent five-symbol 30m candle archive, enriched with taker-buy fields.
2. Start prospective OI collection at a 30-minute cadence, producing one snapshot sequence per symbol.
3. Do not interpret the first several observations; collect at least 30 days before considering an initial screen.
4. Validate data quality first: duplicate local observation times, stale snapshots, missing candle joins, impossible buy volume > total volume, OI jumps, missing 30m bars, and per-symbol coverage.
5. Report sample count, coverage by symbol, state frequency, average/median forward return, positive-return share and dispersion for each horizon.
6. Use chronological periods. The initial screen can describe the collected sample, but any new threshold must be frozen before a later holdout period. Do not choose thresholds using the same OOS period.
7. Account for fees, spread, slippage and funding before any future strategy proposal. The MOEX chart's direction alone is not a trade rule.
8. Only after a stable, cost-adjusted effect survives a fresh holdout can a separate VST shadow/forward test be proposed. No production activation follows automatically.

## 6. What would count as useful evidence

A candidate state must:
- appear often enough across several symbols and months to support analysis;
- show forward-return separation from the unconditional baseline at 30m–9h;
- retain the effect across distinct market regimes, not just one rally or liquidation event;
- remain positive after conservative execution costs and funding;
- avoid dependence on one symbol, one side, or a handful of outliers;
- survive a frozen chronological holdout.

A state with attractive average returns but tiny sample size, poor dispersion, or one-symbol concentration is not an edge.

## 7. Safety / current status

Everything in this branch is research data plumbing and a read-only descriptive analyzer. No production strategy registration, order path, stops, exits, risk percentage, leverage, position sizing, VST routing or REAL switch is modified.

Current state: collector and analyzer are implemented; no prospective OI history has been accumulated yet, so no BingX OI/price/taker-flow result is claimed. The scheduled collection workflow requires explicit owner approval/merge before it starts. The full historical candle data can be enriched from existing archives, but historical OI cannot be backfilled from this current-snapshot endpoint.
