# ASTRA BOT by Gamadril — analytics dashboard

This is a read-only view over repository journals. It does not call BingX,
Telegram or external APIs and does not modify models/.

## Build

    python scripts/zeus_analytics.py
    python scripts/astra_analytics_dashboard.py

The first command updates the deterministic Markdown analytics snapshot.
The second creates reports/astra_dashboard/index.html and dashboard.json.
Both consume the checked-out repository data only.

## Daily refresh

The GitHub Actions workflow astra-analytics-dashboard.yml runs at
03:00 UTC daily (06:00 MSK) and supports workflow_dispatch. It is separate
from the live/paper clocks. It updates a dedicated automation branch and opens
or refreshes a pull request; it does not write directly to master and never
stages models/.

Dashboard generation builds a complete temporary directory and swaps it
into place only after both outputs have been written. A failure during the
build leaves the last published dashboard directory intact.

## Data semantics

- PAPER trades are read from models/zeus_paper_trades.jsonl; partial-close
  legs are coalesced by trade ID using the existing analytics implementation.
- Live exits are classified only with explicit row-level metadata. Missing
  metadata remains UNKNOWN, never guessed as VST or REAL.
- Realized PnL timelines are not account-balance curves. They are cumulative
  sums of recorded closes and are labelled accordingly.
- Session buckets use the project's raw_bucket() helper and MSK.
- Volume-zone and funnel panels use only persisted source events. The
  zone_shadow_approach.json file is a current state snapshot, not history.
- NO_TRADE observations are read from `models/no_trade_observations.jsonl`;
  matched outcomes are read from `models/no_trade_outcomes.jsonl` by stable
  observation ID. The legacy `no_trade_outcomes.json` is read only when the
  JSONL outcome file is absent.
- NO_TRADE panels show reason code, rejection stage, market regime, available
  1/3/6/12/24-bar raw future-return/MFE/MAE summaries, and hypothetical R
  where candidate entry/stop geometry was persisted. Incomplete hypothetical
  windows are excluded from average R.
- Hypothetical R is gross candidate-geometry telemetry without fees; it is
  not an executed-trade result. Raw horizon return is market direction, not
  direction-adjusted strategy PnL. NO_TRADE rows are not counted as scans or
  executed trades.
- Per-symbol scan attempts and exact workflow-run intervals remain unavailable
  unless those events are persisted by their source systems.
- Missing data is shown as "нет данных", not as an invented zero.

## Checks

    python -m pytest tests/test_zeus_analytics.py tests/test_astra_analytics_dashboard.py -q

All dashboard metrics remain informational and do not affect trading decisions.
