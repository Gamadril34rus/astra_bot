# Large-player execution research: Hyperliquid → BingX

**Status (12 Nov 2026): live-data reconnaissance has produced initial observations, but no strategy edge is established.**
All work is isolated from Zeus runtime, live configuration, risk settings and trade execution.

## 1. Research question

Reconstruct how large participants execute parent orders: accumulation/distribution through child orders, aggressiveness, participation, schedule shape, temporary/permanent price impact and post-completion reversion. The objective is to infer repeatable *execution mechanics*, not copy a wallet's position.

## 2. Primary dataset and license

Ironflow's `ironflow/hyperliquid-trades` dataset contains 210,257,579 paired trades (5.3 GB) for 2026-10-03 through 2026-11-08. A row includes timestamp, market, price, size, notional, taker_side, buyer, seller, liquidation, block and tid. The source says each row joins the two fills of one trade; count notional once, not twice.

Ironflow states that files published before 2026-11-10 remain CC BY 4.0; new files from 2026-11-10 onward are delayed 90 days, omit wallet addresses and are CC BY-NC 4.0. Credit: "Ironflow (ironflow.sh)". Verify the file's own publication/version metadata before redistribution.

Sources:
- Dataset card: https://huggingface.co/datasets/ironflow/hyperliquid-trades
- Dataset and license notes: https://ironflow.sh/datasets
- API guide: https://ironflow.sh/guides/hyperliquid-historical-data
- SQL schema: https://api.ironflow.sh/v1/query/schema

## 3. What each data source can identify

The paired-trade Parquet identifies the aggressor address:
- taker_side=buy → buyer is the taker;
- taker_side=sell → seller is the taker.

But Parquet does **not** contain order ID (`oid`), direction, fees or realized PnL. Multiple trade rows can belong to one order. The script `scripts/research/hl_metaorders.py` therefore creates only a **trade-level sequence proxy**, not a paper-equivalent child-market-order reconstruction.

Ironflow SQL's `hl.wallet_fills` / `hl.fills` adds:
- `oid`: order ID, allowing multiple fills belonging to one order to be collapsed;
- `is_taker`: maker/taker role;
- `direction`: Open Long, Close Short, etc. (recorded from 2026-08-01);
- per-fill `fee` and `realized_pnl`.

The `hl.wallet_daily` and `hl.wallet_behavior` tables provide wallet-level daily and 30-day aggregates. `wallet_behavior` labels are rule-based classifications, not ground truth.

**Correct order:** first aggregate fills by `address + market + side + oid`; then group those orders into same-address, same-market, same-direction sequences with inter-order gap below 30 minutes. Keep `direction` as a separate dimension so closing a short is not mistaken for opening a long. Native TWAP slices must be identified separately; do not infer a TWAP solely from 30-second spacing.

Access limits in the API docs: keyless history is 24 hours; a free key gives 30 days; Explorer gives 90 days. The 37-day Parquet archive has addresses but lacks `oid` and direction. The keyless API is enough for a rolling live sample, which is what the preliminary observations below use.

## 4. Reconstructed metaorder baseline

Barone & Lillo (2026) define statistical metaorders as consecutive same-sign market orders from the same address on the same market, with inter-arrival times below 30 minutes. They retain at least 10 child orders and cap duration at 24 hours. Native TWAPs are separately identified using protocol metadata and at least 5 slices.

The paper's measures:
- Q = sum(price × absolute size);
- T = end time − start time;
- N = child market-order count;
- V24 = market notional over the rolling 24h window beginning at parent start;
- φ = Q / V24 (traded fraction);
- Vexec = market notional during the parent execution window;
- η = Q / Vexec (participation rate);
- F = Vexec / V24 (volume-time duration), so φ = η × F.

The Parquet script is explicitly a proxy because it has trade rows rather than `oid`. It records trade count, duration, notional, VWAP, rolling 24h market volume, traded fraction, execution price move and coarse first/last 20% schedule shares. It estimates execution-window market volume from complete 1-minute buckets, so the participation and volume-time fields are approximations at the interval boundaries.

Run:
```bash
pip install -r requirements-research.txt
python scripts/research/hl_metaorders.py \
  --glob 'data/*.parquet' \
  --out results/taker_trade_sequences.parquet
```

Sensitivity for the trade-level proxy: gap thresholds 5/10/20/30 minutes and minimum trade counts 5/10/20. Do not describe those outputs as exact child-order counts.


### Exact order-ID reconstruction via Ironflow API

`scripts/research/ironflow_wallet_metaorders.py` is the order-level path. It queries `hl.wallet_fills`, collapses all fill rows sharing an `oid`, preserves `side` and `direction`, filters to taker orders, and only then forms 30-minute same-side/same-direction parent sequences. It sums fill fees and realized PnL per parent. Zero transaction-hash rows are counted separately as native-TWAP candidates rather than mixed into statistical metaorders.

Example for the keyless rolling 24-hour window:
```bash
python scripts/research/ironflow_wallet_metaorders.py \
  --address 0xYOUR_WALLET_ADDRESS \
  --market BTC-PERP \
  --start 2026-11-11T16:00:00Z \
  --end 2026-11-12T16:00:00Z \
  --out results/btc_wallet_metaorders.json
```

For a free API key, set `IRONFLOW_API_KEY` in the environment; the script automatically uses 6-hour query chunks instead of 1-hour chunks. Ironflow's current pricing page lists 14 days of free-key history, while its SQL endpoint reference says 30 days. The script conservatively guards at 14 days; `--history-days` can override this only after the API's actual tier/notice has been verified. It does not store the key. The grouping and TWAP-exclusion logic has three local unit tests covering order grouping, direction separation, gap splitting and zero-hash exclusion. Funding and unrealized PnL remain separate inputs.

## 5. Preliminary live observations (Ironflow, 12 Nov 2026)

These are **screening observations**, not a backtest. The 30-day wallet profiles are as_of 2026-11-12 and cover the last 30 closed days. Net-after-fees below is realized PnL minus reported fees; it excludes funding and unrealized PnL.

| Wallet label | 30d volume | 30d fills | Maker share | HIP-3 / spot mix | Net after fees | Profit factor | Green-day share |
|---|---:|---:|---:|---:|---:|---:|---:|
| MM-A (`0xecb6…2b00`) | $13.77bn | 5.11m | 82.6% | 32.7% / 8.1% | +$19.59m | 2.01 | 86.7% |
| MM-B (`0x4e23…20c3`) | $3.58bn | 494k | 81.4% | 67.9% / 0% | −$16.81m | 0.38 | 40.0% |
| Winner-A (`0xfc66…ca06`) | $6.49bn | 2.00m | 67.9% | 78.3% / 0.2% | +$23.62m | 2.11 | 86.7% |
| Winner-B (`0x4692…164f`) | $25.62bn | 12.90m | 12.0% | 100% / 0% | +$22.30m | 1.51 | 70.0% |
| Loser-A (`0x0622…8088`) | $6.46bn | 284k | 34.5% | 0% / 0% | −$4.14m | 0.66 | 36.7% |

Product mix matters. MM-A is mixed (about 59% native perps, 33% HIP-3, 8% spot); MM-B is HIP-3-heavy; Winner-A is HIP-3-dominant; Winner-B is entirely HIP-3 in this 30-day snapshot and is **not** a crypto-perp example. Loser-A is the clean native-perp-only counterexample.

Two immediate conclusions:
1. High maker share is not a skill signal by itself: MM-A is strongly profitable on these 30-day realized metrics, while MM-B has almost the same maker share and a large realized loss. They are mixed/HIP-3-heavy profiles, so this is not a pure crypto-perp comparison.
2. Profitable large wallets are not all market makers: Winner-B has only 12% maker share but trades HIP-3 markets, not native crypto perps. Do not use it to infer a crypto-perp execution edge. The filtered 262-wallet cohort below is the relevant crypto-perp comparison.

### Order-level BTC example: Winner-A

For `0xfc66…ca06`, Ironflow `hl.wallet_fills` was grouped by `oid` first, then clustered with the 30-minute rule. On 12 Nov:
- 154 distinct taker orders, 372 fill rows, BTC-PERP buy-side notional $6.589m;
- start 12:59:40 UTC, finish 14:24:32 UTC (84.9 minutes);
- Ironflow `direction` identifies the entire sequence as **Close Short**, not Open Long;
- realized PnL on those fills: +$230.5k; fees: $1.48k;
- BTC last-trade price: 72,846 at sequence start, 71,870 at completion, 71,348 at +30m, 71,331 at +1h.

The BTC market traded about $583.4m during that execution interval; the order's participation was approximately 1.13%. The full 24h BTC-PERP volume was about $3.156bn, so its traded fraction was approximately 0.209% and volume-time duration approximately 18.5%.



During this same Winner-A buy-to-close sequence, the **whole BTC-PERP market** had $212.7m aggressive buys versus $370.7m aggressive sells, for taker delta −$158.0m across 55,134 trades. Market open interest fell from about 40,118 BTC at the start to 39,840 BTC near completion and about 39,028 BTC by the later sample. The wallet's $6.59m buy was therefore a small counterflowing close-short program inside a much larger sell/deleveraging regime. This is precisely why a single participant's buy side must not be interpreted as bullish market demand.

This is the key trap: the public side is **buy**, but the wallet is buying to **close a short**. Price continued lower after the close. Copying the buy side as a long-entry signal would invert the meaning of the flow. Position direction, maker/taker role and realized PnL change the interpretation.

### Order-flow inventory example: MM-A

For `0xecb6…2b00`, the 30-day profile shows 82.6% maker volume. During one BTC window on 12 Nov, wallet fills show both:
- maker Open Long volume about $32.32m and maker Close Long volume about $25.66m;
- taker Open Long volume about $2.65m and taker Close Long volume about $2.39m.

That is two-sided inventory activity, not a single clean directional bet. The taker-only BTC buy sequence alone is not enough to characterize the participant's position.


### Clean native-perpetual wallet examples (HIP-3, spot and prediction shares all zero)

These four addresses are drawn from the same native-perp-heavy screen as the 262-wallet cohort, rather than the mixed/HIP-3 examples above.

| Wallet | 30d volume | Maker share | PF | Realized PnL less fees | Green-day share | Markets |
|---|---:|---:|---:|---:|---:|---:|
| Winner-P1 (`0x023a…2355`) | $1.519bn | 92.0% | 1.78 | +$4.410m | 80.0% | 107 |
| Winner-P2 (`0x3ff5…f705`) | $1.437bn | 82.2% | 3.57 | +$6.580m | 76.7% | 1 |
| Loser-P1 (`0x0622…8088`) | $6.458bn | 34.5% | 0.66 | −$4.140m | 36.7% | 1 |
| Loser-P2 (`0xc179…e0c8`) | $2.029bn | 97.3% | 0.63 | −$2.922m | 40.0% | 24 |

The most useful contrast is Winner-P2 versus Loser-P2: both have very high maker share, but one has strong realized profitability and the other large losses. In the 12 Nov keyless sample, Winner-P2's BTC activity included maker Close Short buys of $57.3m (realized +$1.249m) and maker Open Short sells of $22.4m; its taker Close Short buys added about $83k realized. Loser-P2's ETH/HYPE activity was long-inventory-heavy: on HYPE it had maker Open Long buys of $1.86m, a **single** taker Close Long order of $1.85m split across 277 fills (realized −$214k), and maker Close Long sells of $1.40m (realized −$145k). On ETH, its maker Close Long flow realized about −$36.7k and taker Close Long about −$21.5k.

This gives two concrete lessons:
- maker share alone cannot distinguish a good liquidity provider from one trapped in adverse inventory;
- counting raw trade/fill rows as child orders is wrong: that HYPE market order was one `oid` with 277 fills, not 277 separate parent orders.


The 12 Nov fill-level comparison sharpens the mechanism:
- Winner-P2 (BTC-only native-perp profile): maker Close Short buys were $57.3m across 922 order IDs, with +$1.250m realized PnL; taker Close Short buys added $5.30m and +$83.1k realized. In parallel it had maker Open Short sells of $22.6m and taker Open Short sells of $3.03m. This is short-inventory management with profitable exits, not simply a buy/sell direction signal.
- Loser-P2 (24-market native-perp profile): HYPE maker Open Long buys were $1.89m; its taker Close Long sell was **one order ID with 277 fill rows**, $1.85m notional and −$214.2k realized. Maker Close Long sells added $1.40m and −$144.8k. The order was not flagged as a liquidation. HYPE then moved from about $70.80 at the order to $70.22 at +30m and $69.80 at +1h. ETH closes were also negative.

This is a direct adverse-inventory example: a maker-heavy account accumulated long exposure and later sold it at a loss while the market continued down. The profitable BTC-only account had short exposure and realized gains while closing shorts. The difference is inventory direction and exit quality, not the raw maker percentage.

### Funding is not optional

In the same rolling 24-hour sample, wallet-level funding payments were approximately:
- Winner-A: −$40.5k;
- MM-A: +$9.1k;
- MM-B: −$3.8k.

Funding is across each wallet's markets, not just the BTC example. This is why realized PnL minus fees is still not a complete profitability measure.

### Market sample context

Rolling 24h Hyperliquid perp volume from `hl.trades`:
- BTC-PERP: $3.156bn;
- ETH-PERP: $1.169bn;
- HYPE-PERP: $366.5m;
- SOL-PERP: $213.0m.

These are trade-level market totals counted once per trade. Do not sum `wallet_daily.volume_usd` across wallets to estimate market volume: wallet rows represent both sides of each trade and would double-count.



### Cohort check: maker share versus 30-day outcomes

A broader screening query on `hl.wallet_behavior` was run on the 2026-11-12 snapshot. Filters: at least 20 active days, at least $100m 30-day volume, and each of HIP-3, spot and prediction-market shares below 10% (262 wallets; 90%+ of volume is therefore in native perps).

| Maker share | Wallets | Median PF | Median realized PnL less fees | Avg. green-day share | Net-positive wallets |
|---|---:|---:|---:|---:|---:|
| 0–25% | 82 | 0.774 | −$178.4k | 43.4% | 19/82 |
| 25–50% | 25 | 0.846 | −$151.4k | 51.5% | 8/25 |
| 50–75% | 31 | 1.179 | +$27.4k | 59.7% | 21/31 |
| 75–100% | 124 | 1.106 | +$16.5k | 62.3% | 95/124 |

The rule-based `market_maker` subgroup contained 116 wallets (median maker share 99.6%, median PF 1.106, median net realized PnL +$16.5k; 91/116 positive after fees). The other 146 wallets had median maker share 17.2%, median PF 0.952 and median net realized PnL −$90.4k (52/146 positive).

This is a useful screening correlation, not proof that passive execution causes profitability. The 50–75% bucket slightly outperforms the 75–100% bucket, and individual counterexamples are large. Funding and unrealized PnL are excluded; wallet labels are heuristic. The practical implication is to study *how* passive liquidity is supplied and inventory is managed, not to add a simplistic maker-share threshold to ASTRA.


### Cross-exchange check: BingX BTC-USDT, same 12 Nov window

The same 1-minute window was retrieved from BingX's public swap K-line endpoint. BingX BTC-USDT closes were:
- 12:59 UTC: $72,880.9;
- 14:24 UTC (near the Hyperliquid sequence completion): $71,884.1, −1.37%;
- 14:54 UTC (+30m): $71,368.1, −2.08%;
- 15:24 UTC (+1h): $71,371.8, −2.07%.

BingX's 1m base volume during the 12:59–14:24 interval summed to about 2,552 BTC, or roughly $184.8m using each candle's close as a quote-volume approximation. Hyperliquid's corresponding price path was about $72,846 → $71,870 at completion → $71,348 at +30m → $71,331 at +1h. The two venues show almost the same selloff and post-completion continuation.

This validates that the observed decline was not an isolated Hyperliquid price print.


The same comparison was extended to five symbols. Returns are measured from the 12:55 UTC BingX 5m candle close / Hyperliquid trade price near 12:59:40; the later columns use the nearest 5m close around sequence completion, +30m and +1h.

| Perpetual | Hyperliquid at completion | BingX at completion | Hyperliquid +30m | BingX +30m | Hyperliquid +1h | BingX +1h |
|---|---:|---:|---:|---:|---:|---:|
| BTC | −1.34% | −1.37% | −2.06% | −2.08% | −2.08% | −2.07% |
| ETH | −1.54% | −1.48% | −1.32% | −1.25% | −1.06% | −1.00% |
| SOL | −1.92% | −1.88% | −1.50% | −1.55% | −1.61% | −1.56% |
| HYPE | −1.14% | −1.09% | +0.18% | +0.11% | +0.13% | +0.17% |
| INJ | −0.82% | −0.80% | −0.84% | −0.71% | −2.37% | −2.26% |

The cross-venue agreement is unusually close for this single window. The common selloff is broad, while the post-window paths diverge: HYPE recovers, INJ resumes a sharper decline, and ETH partially mean-reverts. This argues against treating one wallet's BTC action as a standalone market signal. The useful unit of analysis is participant action **plus** multi-asset regime **plus** subsequent response.
 It does **not** identify the wallet on BingX. BingX's public K-line endpoint returned OHLCV fields in this query; it did not return taker-buy quote volume. Its historical-trades endpoint is paged by trade ID with a 100-record maximum, while its public OI endpoint returns current OI, not a historical OI series. Therefore historical BingX CVD/OI reconstruction is a separate data-acquisition problem; do not claim wallet-level replication there.

## 6. What the paper found (Hyperliquid, not yet ASTRA evidence)

The paper studies 201 perpetual markets from 2025-07-28 to 2026-03-23, with more than 641m fills and roughly $1.93tn traded notional. It reports:
- 4.3m statistical metaorders and 465k native TWAP metaorders;
- median statistical metaorder notional about $8.5k, median child count 16;
- median participation rate η about 3.4% for statistical metaorders versus 0.45% for native TWAPs;
- statistical metaorders begin, on average, against the preceding 30-minute move: −7.0 bps signed pre-start return and only 47.7% same-direction; TWAPs begin with the trend (+18.2 bps, 58.4% same-direction);
- statistical metaorders show a non-linear front-loaded/U-shaped schedule: execution rate about 1.5–1.7× uniform in the first decile, 0.6–0.8× in the middle, and 1.1–1.4× in the final decile;
- visible TWAPs execute more uniformly, have lower temporary impact on common support, and attract displayed liquidity while active.

The paper also notes that market-order-based reconstruction can include active liquidity providers when they repeatedly cross the spread. Wallet identity is important: the same side of a trade can be opening, closing, hedging or inventory rebalancing. The paper's findings are observational, not causal proof and not a guaranteed edge on BingX.


### Independent evidence on where the edge comes from

Jia, Zeng, Moallemi & Wang, *Who Loses and Why? Market Microstructure of Perpetual Futures* (2026), use Hyperliquid's on-chain record for the ten most active perpetual markets from 2025-03-27 to 2026-06-30. Their trade-by-trade PnL marks each trade to the midprice two hours later and subtracts fees. They estimate front-end/manual traders lost $1.11bn over 15 months; fees explain about $725m, forced exits about $226m and voluntary trading about $154m. The conference extended abstract reports algorithmic market makers gained about $208m while algorithmic takers were approximately flat.

This is a different sample and methodology from the Barone–Lillo execution-schedule study. Together they point to the same research priority: the durable advantage may be execution quality, liquidity provision, inventory control, fee tier and avoiding forced exits, rather than simply predicting the next price candle. Do not transplant the group PnL numbers to BingX or assume every address labeled a market maker is profitable.

## 7. Required next analyses

1. Obtain the 30-day free Ironflow key if exact order-level reconstruction beyond the keyless 24h window is required; do not buy an Explorer tier without explicit approval. Use `hl.wallet_fills` grouped by `oid`, then reconstruct sequences.
2. Build a wallet cohort with repeat activity across BTC/ETH/SOL/HYPE, net realized PnL, fees, funding, maker share, flow imbalance, liquidation count and active-day stability. Avoid selecting on one 30-day PF snapshot alone.
3. Reconstruct order-level metaorders with `direction` split (Open Long, Close Short, etc.), and separately identify TWAP slices.
4. Measure parent-order response at +30m/+1h/+2h/+4h/+9h, with market prices, OI, funding, liquidation flow and order-book depth. Compare opening vs closing flows.
5. Test the execution footprint on BingX USDT-M. BingX does not expose participant identities publicly; only aggregate analogues can be tested.
6. Join ASTRA Zone Shadow events only after the source journal has been located and validated. Test approach → zone entry → absorption/break/fakeout/retest and forward returns at 30m/1h/2h/4h/9h, with fees and funding.
7. Publish reproducible results with sample coverage, data gaps, event counts, confidence intervals, costs, IS/validation/OOS and negative findings.



### Forced-liquidation case: a whale-sized sell is not a discretionary signal

A separate native-perp wallet (`0x6046…b21d`, 30-day profile: 5 active days, PF 0, realized PnL −$5.30m, fees $29k) produced a BTC-PERP sell event at 13:09:52–13:10:01 UTC:
- 822 fill rows, but only **2 order IDs**;
- $38.77m notional, classified `Close Long` and `is_liquidation=true`;
- realized loss −$5.25m;
- the whole BTC market traded $64.75m aggressive sells versus $5.36m buys in those 9.4 seconds (net taker delta −$59.39m). The wallet accounted for about 60% of aggressive sell notional.

BTC traded about $72,464 at the event, $72,568 at +30m (+0.14%), $72,300 at +1h (−0.23%) and $71,341 at +2h (−1.55%). OI fell from roughly 40,067 BTC to 39,978 BTC around the event, then recovered to 40,349 BTC by +1h.

This is a forced long liquidation, not a skilled participant choosing to open a short. The initial 30m bounce did not persist; by +2h price was materially lower. A trade-level scan would have counted 822 child trades and mistaken the event for a 9-second parent program. OID plus the liquidation flag reveals the actual mechanism. Liquidations must be a separate event class in the research, not mixed into the smart-money cohort.


### BingX prospective OI archive: first cross-venue positioning read

The separate `research/positioning-data` branch contains a BingX USDT-M open-interest capture started 2026-11-10 09:51 UTC. As of 2026-11-12 15:57 UTC it held 105 snapshots per symbol, roughly 30-minute spacing, for BTC/ETH/SOL/HYPE/INJ. This is a **prospective** archive, not retroactive historical OI. The accompanying 30m candle archive is gap-free for all five symbols and reaches the 15:00 UTC bar on 12 Nov.

Nearest snapshots around the Hyperliquid BTC liquidation at 13:09:52 UTC:

| BingX symbol | OI before (~12:50 UTC) | OI after (~13:20 UTC) | Change |
|---|---:|---:|---:|
| BTC | 842.71m | 884.48m | +4.96% |
| ETH | 332.45m | 341.50m | +2.72% |
| SOL | 96.68m | 95.70m | −1.01% |
| HYPE | 30.56m | 31.52m | +3.16% |
| INJ | 3.492m | 3.494m | +0.04% |

By the next snapshot around 14:22 UTC, ETH OI had increased further to 377.66m (+10.6% from 13:20), while BTC was almost flat at 882.94m (−0.17%), HYPE at 31.43m (−0.29%), SOL at 95.64m (−0.07%) and INJ at 3.438m (−1.60%).

This is a useful early indication that positioning response was not uniform across assets. However, the capture is only 105 points deep and the raw BingX `openInterest` unit has not been independently reconciled with contract size; the archive currently labels it as base quantity, but absolute magnitudes do not look like literal BTC/ETH coin amounts. **Use within-symbol percentage changes only; do not use absolute OI notional or cross-symbol level comparisons until units are verified.** The API's public response and contract metadata need a dedicated unit audit before the archive is treated as a calibrated OI series.


### First BingX OI-only event test (exploratory, not a strategy result)

I aligned the 106 CSV snapshots per symbol with BingX 5m closes (last closed candle at each snapshot), then classified each consecutive snapshot interval by price direction and raw OI percentage change. I kept `|price move| ≥0.05%`, `|OI move| ≥0.25%`, and gaps ≤90 minutes. The manifest still says 105 rows, so it is one snapshot behind the CSVs.

| State | Events | Next 30m avg | Next 1h avg | Next 2h avg | Next 4h avg | Next 9h avg |
|---|---:|---:|---:|---:|---:|---:|
| Price up, OI up | 71 | −0.14% | −0.26% | −0.13% | −0.09% | −0.05% |
| Price down, OI up | 63 | +0.02% | +0.06% | −0.43% | −0.30% | −0.01% |
| Price up, OI down | 121 | −0.07% | −0.15% | −0.20% | −0.33% | −0.69% |
| Price down, OI down | 127 | +0.04% | +0.08% | +0.03% | −0.26% | −0.36% |

At a stricter 1% OI-change threshold, the price-down/OI-up group had 38 observations: mean +0.02% at +30m, +0.04% at +1h, −0.54% at +2h and −0.35% at +4h. That superficially resembles an initial bounce followed by continuation, but the per-symbol breakdown is inconsistent and sample sizes fall to 2–14 events per symbol. Price-up/OI-up did not show continuation; the other states were mixed.

**Verdict:** OI direction alone does not yet produce a robust, cross-symbol 30m–9h signal. These events overlap, span only about 2.3 days, omit taker delta, and have no fees/funding adjustment. The raw OI unit is unresolved. This is a useful data-acquisition pilot, not a profitable strategy result. We need a longer archive and a taker-flow measure before drawing a trading conclusion.

## 9. Candidate ASTRA translation to test (not a live rule)

The evidence suggests the first strategy candidate should be a **position-build versus position-exit classifier**, not a raw whale-following signal.

On Hyperliquid, the ground-truth classes are available:
- buy + Open Long;
- buy + Close Short;
- sell + Open Short;
- sell + Close Long.

On BingX, participant direction is not public. Only aggregate states can be inferred probabilistically:
- price rising + net aggressive buying + OI rising → likely new long-side leverage;
- price falling + net aggressive selling + OI rising → likely new short-side leverage;
- price rising + net aggressive buying + OI falling → likely short covering;
- price falling + net aggressive selling + OI falling → likely long reduction/liquidation.

These are market-wide heuristics, not wallet attribution. They must be checked against the actual BingX contract data and not treated as exact position labels.

Proposed research test:
1. Build 1m/5m market-state episodes from taker delta, OI change, funding, price impact, volume and book depth.
2. Label Hyperliquid episodes using exact `direction`, maker/taker role and OID-level metaorders.
3. Test whether opening-flow episodes with rising OI and persistent signed impact outperform closing-flow episodes after 30m/1h/2h/4h/9h, net of fees and funding.
4. Separate absorption (large aggressive flow, weak price progress, replenishing opposite-side depth) from continuation (price progress and OI expansion).
5. Join ASTRA Zone Shadow approach/entry/absorption/break/retest states only as a research dimension.
6. Validate the same aggregate states on BingX. Do not copy the Hyperliquid wallet's side or assume its position is visible there.

No threshold, entry, exit or risk parameter is approved. The purpose is to generate a falsifiable strategy test; Zeus remains untouched.

## 10. Safety boundary

This folder and script are research-only. Do not wire outputs into live Zeus, enable a strategy, change risk parameters or switch REAL on. Any eventual strategy proposal must first pass independent walk-forward/OOS evaluation and a separate owner-approved integration review.

## 9. Research source

Barone, Davide & Lillo, Fabrizio (2026), "Trading in the Sunshine or in the Shade: Market Impact and Adverse Selection on Hyperliquid", https://arxiv.org/html/2606.15715. All transfer to BingX remains a hypothesis to test.
