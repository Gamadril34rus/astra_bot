# PR history addendum — 2026-11-10

This addendum records the research and maintenance decisions made after the main PR history was last updated. Times are Moscow time (UTC+3).

| PR | Date (MSK) | Result | Decision / evidence |
|---|---|---|---|
| [#348](https://github.com/Gamadril34rus/astra_bot/pull/348) | 2026-11-10 11:49 | **Merged** (630a2f8c89a6) | MOEX-inspired BingX positioning shadow. Adds public total-OI snapshots, taker-buy kline fields, closed-candle alignment, descriptive forward-return analysis (30m/1h/2h/4h/9h), tests and an isolated data branch/workflow. Research-only; no strategy registration, orders, risk, VST or REAL changes. Quality Gates and Candle Archive PR checks passed on head 2280055d77df39801a0e8a8cf4bf2f59448444fd. |
| [#347](https://github.com/Gamadril34rus/astra_bot/pull/347) | 2026-11-09 | **Open / retained** | 4h-regime 30m pullback continuation hypothesis. Kept as a separate research candidate; no promotion or live activation. |
| [#346](https://github.com/Gamadril34rus/astra_bot/pull/346) | 2026-11-09 | **Closed without merge** | Previous-UTC-day range breakout. Partial BTC/ETH/SOL screen showed only 6 IS and 1 validation trade, negative PF/expectancy; HYPE/INJ absent. Insufficient evidence and not selected. |
| [#345](https://github.com/Gamadril34rus/astra_bot/pull/345) | 2026-11-09 | **Closed without merge** | Confirmed breakout/retest. Partial screen: 29 trades, PF 0.81 and negative expectancy; OOS 3 trades, validation 2. Costs erased gross edge. Not selected. |
| [#344](https://github.com/Gamadril34rus/astra_bot/pull/344) | 2026-11-09 | **Open / retained** | Shared 30m perpetual strategy research lab, candle/funding/order-book archive and replay infrastructure. Retained as supporting research infrastructure, not as a selected profitable strategy. |
| [#343](https://github.com/Gamadril34rus/astra_bot/pull/343) | 2026-11-09 | **Closed without merge** | Adaptive regime portfolio rejected: OOS PF 0.5406, expectancy -0.24751%/trade; 1.5x-cost OOS PF 0.4486. No positive months. Do not tune against the same OOS sample. |
| [#341](https://github.com/Gamadril34rus/astra_bot/pull/341) | 2026-11-09 | **Closed without merge** | 4h breakout/retest candidate failed promotion: corrected Binance-reference OOS PF about 1.00 (102 trades), below the pre-set PF 1.2 gate; original claimed result invalidated by source-data mismatch. |
| [#340](https://github.com/Gamadril34rus/astra_bot/pull/340) | 2026-11-09 | **Open / retained** | Live TP1 fraction persistence fix. Retained for execution-safety review; not part of positioning research. |
| [#339](https://github.com/Gamadril34rus/astra_bot/pull/339) | 2026-11-09 | **Open / retained** | Hedge-side stop/orphan-position protection fix. Retained because it addresses position-protection correctness; do not close as redundant without a verified replacement. |
| [#299](https://github.com/Gamadril34rus/astra_bot/pull/299) | 2026-11-01 | **Closed without merge** | Dependabot Python 3.15-slim bump left stale/incompatible with the repository Dockerfile still based on Python 3.12-slim. Revisit only with a deliberate runtime upgrade and full compatibility validation. |

## Positioning shadow operating status

The collector is merged into master. Its workflow schedule is 03 and 33 minutes past each UTC hour, but GitHub scheduled starts are best-effort and may be delayed. The persistent branch is research/positioning-data. The first seed requires workflow_dispatch with initialize_history=true; subsequent runs append current OI snapshots and refresh the report.

The external cron-job.org trigger has **not** been configured from this session: no supported external HTTP/API action is available here. Do not represent the collector as actively seeded until a successful initialization run and non-empty per-symbol OI archives are verified. A separate external scheduler should call the GitHub Actions workflow-dispatch endpoint every 30 minutes; its GitHub token and cron-job.org API key must be stored as secrets, never committed or pasted into repository documentation.

## Research cadence decision

Do not wait 30 days to inspect the first observations. Use staged review:
- first 3–5 snapshots per symbol: verify timestamps, OI units, candle joins, duplicates and data freshness;
- after roughly 12 snapshots (~6 hours): first exploratory state/forward-return view, explicitly low-confidence;
- after 48–96 snapshots (~1–2 days): interim per-symbol and pooled statistics with unconditional baselines and conservative cost sensitivity;
- continue accumulating and validate any frozen hypothesis on later chronological data. Early observations may expose bugs or obvious patterns, but cannot establish a robust edge or authorize production use.
