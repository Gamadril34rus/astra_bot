# MOEX-inspired positioning-pressure shadow for BingX USDT-M

Status: research-only data collection and descriptive analysis. No strategy entry, exit, sizing, or risk integration.

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

Therefore this project does **not** claim to reproduce MOEX participant positioning. Its primary BingX research features are:
1. Total open interest (OI) change between timestamped public snapshots.
2. Price change across the latest fully completed 30m candle.
3. Forward price returns after the observation, for 30m, 1h, 2h, 4h and 9h.
4. Taker-buy imbalance as an optional refinement only when the actual returned candle contains valid taker-buy fields.

Taker-buy volume identifies the aggressive side of executed volume, not who opened/closed a position. OI is total outstanding exposure, not net long or net short. Every open futures contract has both a long and a short counterparty. Price/OI states remain descriptive market-pressure heuristics, not participant identity or proof of trade direction.

## 3. Collection contract

### Candle enrichment

The candle parser preserves BingX v3 kline array fields:
- index 8: number of trades,
- index 9: taker-buy base-asset volume,
- index 10: taker-buy quote-asset volume.

The append-only archive adds optional columns:
- `taker_buy_volume`,
- `taker_buy_quote_volume`.

Older OHLCV rows can be re-fetched with `--refresh-optional-fields`, but this is not a guarantee that optional fields exist in the response. In the current five-symbol 30m archive, the observed object-form responses have `quote_volume=0`, `trades_count=0`, and blank `taker_buy_volume` / `taker_buy_quote_volume`. These values must not be interpreted as zero aggressor flow. Existing OHLCV values remain immutable and absent optional values remain unavailable. The analyzer now retains valid price/OI observations with `taker_unknown` rather than discarding them. Taker-flow states are added only for rows where the exchange actually supplies valid values. The archive retries BingX 100410 with bounded cooldowns and uses a conservative request pace.

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
- `taker_buy_share_pct` and `taker_imbalance_pct`: optional; calculated only where valid taker-buy and total volume are both present. Missing values stay unknown.
- `open_interest_usdt_est`: base-unit OI multiplied by the latest closed 30m close; this is a notional estimate, not an exchange-reported USD field.
- Forward close-to-close returns for 30m, 1h, 2h, 4h and 9h after the matched closed candle.

When taker flow is missing, the analyzer keeps the observation in one of four primary descriptive price/OI states and labels taker flow as unknown. When taker flow is available, it adds the buy/sell/balanced refinement. Missing taker fields never cause a valid OI/price observation to be discarded.

Example interpretation only:
- price up + OI up + buy imbalance positive = price advanced while total exposure expanded and buyers were more aggressive;
- price up + OI down = price advanced while total exposure contracted, which can be consistent with short closing;
- price down + OI down = price fell while total exposure contracted, which can be consistent with long closing;
- price down + OI up + sell imbalance positive = price fell while total exposure expanded and sellers were more aggressive.

These are not unique explanations. The buyer/seller that initiated each trade may be opening or closing, and the OI endpoint does not classify them. The analysis reports conditional forward returns; it does not create a long/short signal.

## 5. Staged evaluation protocol

The goal is to inspect useful evidence early without pretending a tiny sample proves an edge.

1. Preserve the five-symbol BingX 30m OHLCV archive; do not make historical taker-buy enrichment a prerequisite for the primary OI/price screen.
2. Collect prospective OI snapshots on a 30-minute cadence for BTC, ETH, SOL, HYPE and INJ.
3. **Data-integrity check after the first 3–5 snapshots per symbol:** verify timestamps, OI units, duplicates, freshness, candle joins and optional-field coverage. Missing taker fields are recorded as unknown, never as zero.
4. **First exploratory screen after roughly 12 valid snapshots (~6 hours):** inspect state frequency, direction and available forward returns. Mark the result low-confidence; use it to find obvious behavior or collection bugs, not to declare profitability.
5. **Interim screen after 48–96 valid snapshots (~1–2 days):** report per-symbol and pooled sample counts, state frequency, average/median forward return, positive-return share, dispersion, unconditional baselines and conservative cost sensitivity.
6. Continue collection. Use chronological splits; freeze any candidate threshold before evaluating later holdout data. Do not select thresholds using the same OOS period.
7. Validate data quality continuously: duplicate local observation times, stale snapshots, missing candle joins, optional-field coverage, OI jumps, missing 30m bars and per-symbol coverage.
8. Account for fees, spread, slippage and funding before any strategy proposal. The MOEX chart's direction alone is not a trade rule.
9. Only after a stable, cost-adjusted effect survives a later chronological holdout can a separate VST shadow/forward test be proposed. No production activation follows automatically.

Early screens are deliberately exploratory. Six hours can reveal a broken join or an interesting first pattern; it cannot establish a durable trading edge. Evidence must accumulate, but there is no reason to wait 30 days before looking at the first results.

## 6. What would count as useful evidence

A candidate state must:
- appear often enough across several symbols and distinct market regimes to support analysis;
- show forward-return separation from the unconditional baseline at 30m–9h;
- remain positive after conservative execution costs and funding;
- avoid dependence on one symbol, one side, or a handful of outliers;
- survive a frozen chronological holdout.

A state with attractive average returns but tiny sample size, poor dispersion, or one-symbol concentration is not an edge.

## 7. Safety / current status

Everything here is research data plumbing and a read-only descriptive analyzer. No production strategy registration, order path, stops, exits, risk percentage, leverage, position sizing, VST routing or REAL switch is modified.

PR #348 was merged to master on 2026-11-10 (merge commit `630a2f8c89a6ae071c19404b818f8dbbedca0ce0`). The collector workflow is present in master at `.github/workflows/positioning-shadow.yml`; the persistent data branch is `research/positioning-data`. External `workflow_dispatch` runs are visibly arriving at roughly 30-minute intervals.

As of 2026-11-11 06:30 UTC, the data branch contains 39 OI snapshots for each of BTC, ETH, SOL, HYPE and INJ. The earlier summary still showed zero usable observations because the analyzer incorrectly required taker-buy data that was blank in the archived 30m candles.

The collector now uses a shallow checkout (`fetch-depth: 1`) for the persistent data branch. The previous full-history checkout took about 25 minutes before the first OI request, which often made the latest completed 30m candle older than the analyzer's 20-minute freshness limit. The shallow checkout avoids downloading irrelevant Git history; the freshness and 90-minute OI interval gates remain unchanged.

After the five-symbol archive is seeded, collection appends closed candles, captures current OI and rebuilds the price/OI report. It no longer performs a full historical taker-buy re-fetch on the assumption that those fields must be present. The endpoint's current captured object-form data has not supplied them. Historical OI cannot be backfilled from the current-snapshot endpoint.

These collection and analyzer changes remain research-only. They do not register strategies or change orders, risk, leverage, stops, exits, position sizing, VST routing or REAL routing.
