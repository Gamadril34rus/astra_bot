# ZEUS analytics

Read-only, deterministic analytics for the Zeus project. The generator reads
selected models JSON/JSONL sources and structured JSON/JSONL reports. It writes
only reports/zeus_analytics/summary.md.

## Manual run

    python scripts/zeus_analytics.py

For a separate checkout or fixture directory:

    python scripts/zeus_analytics.py --root /path/to/checkout

The script is not connected to paper/live clocks, COPY_ZEUS, GitHub Actions
schedules or cron-job.org. It uses the same raw_bucket() helper as the session
profile module and does not call any network/API/Secrets.

## Source and contour rules

- Paper trades come from models/zeus_paper_trades.jsonl. Partial close rows are
  coalesced by trade id before PF, winrate, average R, expectancy and fees.
- VST and REAL exits are classified only when the row explicitly contains
  environment metadata. Live rows without it are reported separately as
  live / UNKNOWN; they are never assumed to be VST.
- Zone observations come from zone_shadow, zone_shadow_summary and
  zone_shadow_error events in models/zeus_live_journal.jsonl. The
  zone_shadow_approach.json file is shown only as a current snapshot, not as a
  historical approach counter.
- Funnel stages start at pending_signal and arm_summary. Raw per-symbol scan
  attempts are not persisted and therefore are reported as unavailable.
- Tick intervals are inferred from journal timestamps grouped within 120
  seconds. They are not exact GitHub Actions run counts.
- COPY_ZEUS encrypted Actions artifacts and console logs are outside these
  readable inputs. Archive ticks and 100410 failures are included only when a
  structured JSON/JSONL report is present under reports/.

Every quantitative table includes period, source, contour and trade-count
context. Samples below 20 trades are labeled "мало данных". Empty sections
say "нет данных за период" rather than showing placeholder zeros.

The generator never writes to models/, never overwrites existing reports, uses
no network access and does not change strategy, risk, runtime or workflows.
