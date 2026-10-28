# Starfleet facility pipeline

Python CLI that reads Starfleet receivables and collections exports, persists
append-only events in Postgres, allocates payments idempotently, and writes
the receivables and pool reports.

**Corrections (follow-up):** when an existing receivable arrives with a different
`face_value` or `number_of_installments`, the pipeline builds a **new** installment
schedule (Section 5 rules on the new terms), keeps the prior schedule and
allocations for audit, re-applies collections onto the current plan, and reports
the correction. See [submission/corrections-note.md](submission/corrections-note.md).

## Quick start

```bash
docker compose up -d
pip install -e ".[dev]"

fence-pipeline run \
  --receivables data/receivables.csv \
  --collections data/collections.csv \
  --output-dir out/
```

Reports land in `out/`:

- `payment_schedule_report_run_<id>.csv` — installments, settlement, which `payment_id`(s) hit each installment (long; CSV only)
- `receivables_position_report_run_<id>.csv` — per-receivable outstanding, status, eligibility (long; CSV only)
- `exceptions_report_run_<id>.csv` — every invalid/uncertain row for Starfleet to correct and resend
- `pool_report_run_<id>.csv` — counts, concentration, exceptions, vs previous
- `pool_report_run_<id>.html` — short human-readable pool + exceptions with links into the CSV sheets

Resume a failed run:

```bash
fence-pipeline run \
  --receivables data/receivables.csv \
  --collections data/collections.csv \
  --output-dir out/ \
  --resume <run_id>
```

Database URL defaults to `postgresql://fence:fence@localhost:5432/fence`
(Compose). Override with `FENCE_DATABASE_URL` or `--database-url`.

## What the pipeline does

1. Soft-fail ingest of both CSVs (bad rows → exceptions, run continues)
2. Append receivable events every run; insert payments once by `payment_id`
3. Upsert the live `receivables_current` book (one row per ID); inactivate IDs missing from tonight’s full file
4. Build equal-installment schedules (remainder on last; end-of-month clamp). On term drift, bump `current_schedule_version`, insert a new plan, keep the old one
5. Allocate only unapplied payments to earliest unpaid installments on the **current** schedule version (waterfall through the final installment; leftover → excess exception)
6. Derive status, reconcile outstanding vs export, evaluate eligibility (per-`run_id` results)
7. Concentration (40% of eligible outstanding by sector) and borrowing base (85%) — AEB uses **declared** outstanding balance
8. Emit reports; print per-stage timings

Only one pipeline run may be `running` at a time (single writer on the live book).

`exceptions_report_run_*.csv` includes `source=correction` (what changed) and
`source=allocate-excess` rows (with an `amount` column) when cash exceeds the
rebuilt schedule. Orphan payments use `source=allocate-orphan`.

Persistence uses **SQLAlchemy 2.0** ORM models (`fence_pipeline.models`) with
Postgres via `psycopg`. Schema is created from model metadata on startup
(`Base.metadata.create_all`), with additive ALTERs in `ensure_schema` for
`current_schedule_version` / installment `schedule_version`. SQL under
`migrations/` remains a readable reference.

## Tests

```bash
pytest
```

Financial number checks live under `tests/support/` (used by `tests/test_financial_truth.py`).

### Scale notes

Observed full-file run on sample data in `data/` (~50k receivable rows, ~231k payments),
wall clock ~104s on a local Postgres:

| Stage | Seconds |
|-------|--------:|
| ingest_receivables | 8.6 |
| ingest_collections | 38.5 |
| project_current | 0.6 |
| build_schedules | 3.3 |
| allocate_payments | 49.4 |
| evaluate | 0.8 |
| write_reports | 2.9 |

Timings are printed on every run and stored on `pipeline_runs.timings`. Soft-fail
also surfaced real data issues (e.g. rows missing `obligor_risk_score`; orphan
payments for those skipped receivables).

If volume grows ~10×, likely next steps:

- `COPY` for CSV ingest instead of row inserts (collections is the ingest bottleneck)
- Set-based SQL for installment `amount_applied` updates (allocation is the other hotspot)
- Larger commit batches; optional parallel schedule build by id ranges

## Docs

- [docs/design_notes.md](docs/design_notes.md)
- [docs/open_questions.md](docs/open_questions.md)
- [facility-documents/](facility-documents/)
