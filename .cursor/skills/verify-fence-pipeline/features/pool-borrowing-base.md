# Pool borrowing base

Pool totals: processed/eligible/ineligible counts, sector concentration vs 40%
cap, Aggregate Eligible Balance, excess concentration, borrowing base (85%),
and deltas vs the previous successful snapshot in the same database.

## Sub-features

- Current metrics block in `pool_report_run_<id>.csv`
- Concentration rows per sector (`within_cap`, `excess`)
- Exceptions section
- Previous / delta when a prior `pool_snapshots` row exists

## How to get to it (user POV)

Run the pipeline; open `pool_report_run_<id>.csv` (section/key/value CSV).

## Driving it with the CLI harness

```bash
.cursor/skills/verify-fence-pipeline/helpers/drive-pipeline-run.sh
# grep borrowing_base / concentration in $EVDIR/out/pool_report_run_*.csv
```

For previous/delta, drive twice against `fence_verify` without truncating
between runs (skip `cleanup.sh` between the two runs).

## Gotchas

- Facility limit is reported as headroom info; BB is not hard-capped at 250M
  in v1.
- Deltas compare to the latest prior `pool_snapshots` in **this** DB — mixing
  fixture and full-data runs in `fence` pollutes comparisons; use `fence_verify`.
