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

Therefore this project does **not** claim to reproduce MOEX participant positioning. It captures the nearest observable BingX proxy:
1. Total open interest (OI) change between timestamped public snapshots.
2. Taker-buy share within each completed 30m candle.
3. The price return on that same completed candle.
4. Forward price returns after the observation, for 30m, 1h, 2h, 4h and 9h.

Taker-buy volume identifies the aggressive side of executed volume, not who opened/closed a position. OI is total outstanding exposure, not net long or net short. Every open futures contract has both a long and a short counterparty. Combining OI change with price and taker flow is a market-pressure heuristic, not participant identity or a proof of trade direction.

## 3. Collection contract

### Candle enrichment

The candle parser preserves BingX v3 kline array fields:
- index 8: number of trades,
- index 9: taker-buy base-asset volume,
- index 10: taker-buy quote-asset volume.

The append-only archive adds optional columns:
- `taker_buy_volume`,
- `taker_buy_quote_volume`.

Older OHLCV rows can be re-fetched and enriched with `--refresh-optional-fields`. Existing OHLCV values remain immutable. Unknown optional values remain blank, not zero. The archive retries BingX 100410 with bounded cooldowns and uses a conservative request pace.

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

## 5. Staged evaluation protocol

The goal is to inspect useful evidence early without pretending a tiny sample proves an edge.

1. Preserve the parent five-symbol 30m candle archive and enrich it with taker-buy fields.
2. Collect prospective OI snapshots on a 30-minute cadence for BTC, ETH, SOL, HYPE and INJ.
3. **Data-integrity check after the first 3–5 snapshots per symbol:** verify timestamps, OI units, duplicates, freshness, candle joins and missing values. Fix data-contract errors immediately.
4. **First exploratory screen after roughly 12 snapshots (~6 hours):** inspect state frequency, direction and available forward returns. Mark the result low-confidence; use it to find obvious behavior or collection bugs, not to declare profitability.
5. **Interim screen after 48–96 snapshots (~1–2 days):** report per-symbol and pooled sample counts, state frequency, average/median forward return, positive-return share, dispersion, unconditional baselines and conservative cost sensitivity.
6. Continue collection. Use chronological splits; freeze any candidate threshold before evaluating later holdout data. Do not select thresholds using the same OOS period.
7. Validate data quality continuously: duplicate local observation times, stale snapshots, missing candle joins, impossible buy volume > total volume, OI jumps, missing 30m bars and per-symbol coverage.
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

PR #348 was merged to master on 2026-11-10 (merge commit `630a2f8c89a6ae071c19404b818f8dbbedca0ce0`). The collector workflow is now present in master at `.github/workflows/positioning-shadow.yml`. Its GitHub schedule is at minutes 03 and 33 UTC; scheduled starts are best-effort and can be delayed. The persistent data branch is `research/positioning-data`.

The workflow's first historical seed currently requires a `workflow_dispatch` run with `initialize_history=true`. After seeding, scheduled runs append current OI snapshots and rebuild the descriptive report. Until a successful initialization and non-empty per-symbol OI archives are verified, the research must be treated as **not yet collecting**. External cron-job.org has not been configured from this session because no supported external HTTP/API action is available here. The external trigger should call GitHub's workflow-dispatch endpoint every 30 minutes; store its GitHub Actions token and cron-job.org API key as secrets, never in repository files.

Historical candle data can be enriched from existing archives, but historical OI cannot be backfilled from the current-snapshot endpoint.


### One-time taker-buy history backfill

The collector checks for the persistent marker `data/bingx_usdm/.positioning_taker_buy_backfill_v1`. If the marker is absent after the five-symbol 30m archive is seeded, the next successful collection run re-fetches the complete archived 30m candle window with `--refresh-optional-fields`, then writes the marker. The marker is committed to `research/positioning-data`, so ordinary 30m append runs do not repeatedly download the full history or accidentally re-mark the archive as unrefreshed. The marker is written only after the full five-symbol refresh command succeeds. The existing candle archive tests cover immutable OHLCV preservation and enrichment of legacy rows.
