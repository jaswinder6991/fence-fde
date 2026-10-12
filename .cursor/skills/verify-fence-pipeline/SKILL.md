---
name: verify-fence-pipeline
description: >-
  Verify the Starfleet fence-pipeline CLI (receivables/collections ingest →
  Postgres → payment schedule, receivables position, and pool reports). Use when
  proving pipeline behavior, report shape, idempotent payment allocation, or
  Ops run paths after code changes.
---

# Verify fence-pipeline (CLI)

Primary surface: **CLI** (`fence-pipeline`). There is no web UI or HTTP API.
Supporting surfaces: Postgres (Compose), CSV report files under an output dir.

Agents read this cold. Prefer the helpers in this skill over inventing ad-hoc commands.

## Launch

Deps + package (once per machine/checkout):

```bash
cd /Users/jassification/Documents/fence-fde
docker compose up -d
pip install -e ".[dev]"
```

Ready when:

```bash
docker compose exec -T db pg_isready -U fence -d fence
fence-pipeline run --help
```

Verification uses an **isolated database** `fence_verify` on the same Postgres
container (port 5432) so it does not share `pipeline_runs` / allocations with
ad-hoc Ops runs against the default `fence` DB. Create it once:

```bash
.cursor/skills/verify-fence-pipeline/helpers/ensure-verify-db.sh
```

There is no long-lived app server. Each drive is a short-lived CLI process.

**Teardown** of what Launch started: only stop Compose if *you* started it for
this verification and the user did not already have it running:

```bash
# Only if this verification session brought Compose up
docker compose stop
```

Do **not** `docker compose down -v` (wipes the volume). Prefer leaving Compose up.

## Doctor

Read-only health check:

```bash
.cursor/skills/verify-fence-pipeline/helpers/doctor.sh
```

Pass means: Compose `db` is healthy, `fence_verify` exists, `fence-pipeline` is
importable, fixture CSVs exist. If doctor fails, fix Launch before driving.

**Isolation rule:** refuse to “double-drive” by truncating or writing into the
default `fence` database used for manual/full-data runs. Always pass
`--database-url` from `helpers/verify-env.sh` (or source that script).

## Drive

Harness: plain shell + CLI. Fixtures live at `tests/fixtures/`.

Source env then run a mapped feature (see `features/`):

```bash
cd /Users/jassification/Documents/fence-fde
source .cursor/skills/verify-fence-pipeline/helpers/verify-env.sh
# EVDIR is set to a fresh evidence directory

fence-pipeline run \
  --receivables tests/fixtures/receivables.csv \
  --collections tests/fixtures/collections.csv \
  --output-dir "$EVDIR/out" \
  --database-url "$FENCE_VERIFY_DATABASE_URL" \
  | tee "$EVDIR/cli.log"
```

Stable handles:

| Handle | Meaning |
|--------|---------|
| `run_id=<n>` printed at end of CLI | Successful run id |
| `$EVDIR/out/payment_schedule_report_run_<n>.csv` | Schedule + payment hits |
| `$EVDIR/out/receivables_position_report_run_<n>.csv` | As-of export position |
| `$EVDIR/out/pool_report_run_<n>.csv` | Pool / BB / concentration |
| Stage lines `ingest_receivables:`, `allocate_payments:`, `completed run` | Progress |

One-shot wrapper that does env + run + basic assertions for the happy path:

```bash
.cursor/skills/verify-fence-pipeline/helpers/drive-pipeline-run.sh
```

Resume (only after a failed run against `fence_verify`):

```bash
fence-pipeline run \
  --receivables tests/fixtures/receivables.csv \
  --collections tests/fixtures/collections.csv \
  --output-dir "$EVDIR/out" \
  --database-url "$FENCE_VERIFY_DATABASE_URL" \
  --resume <run_id>
```

Feature map (authoritative coverage list):
[features/README.md](features/README.md)

## Evidence

Proof root (survives cleanup):

```text
.cursor/skills/verify-fence-pipeline/evidence/<UTC-timestamp>/
```

Capture at minimum:

1. `cli.log` — full stdout/stderr of the CLI drive (includes stage timings and `run_id=`)
2. The three report CSVs from `--output-dir`
3. Optional: `db-snapshot.txt` from `helpers/snapshot-db.sh` (row counts / sample allocations)

**Proof standards**

- Exercise the real Ops path: `fence-pipeline run` on CSVs → reports on disk. Do not call internal Python setters as a substitute for the CLI.
- Capture the action (`cli.log`) and resulting state (report CSVs; DB snapshot when proving allocation idempotency).
- Side effects to verify: report files exist with expected headers/rows; `payment_events` / `payments_applied` / `payment_allocations` consistent with schedule report; re-run does not increase `amount_applied`.
- Mocks: none required. Postgres is the real boundary (local Compose). Do not point verification at production.

## Cleanup

```bash
.cursor/skills/verify-fence-pipeline/helpers/cleanup.sh
```

Cleanup:

- Truncates tables in **`fence_verify` only** (never the `fence` DB)
- Removes scratch dirs under `/tmp/fence-pipeline-verify-*` if present
- Does **not** delete `evidence/` artifacts
- Does **not** kill Docker by process name; does not stop Compose unless you started it solely for verify and choose to `docker compose stop`

## Helpers

All under `.cursor/skills/verify-fence-pipeline/helpers/` (executable):

| Script | Purpose |
|--------|---------|
| `verify-env.sh` | Sourceable: sets `FENCE_VERIFY_DATABASE_URL`, creates `EVDIR` |
| `ensure-verify-db.sh` | Creates Postgres database `fence_verify` if missing |
| `doctor.sh` | Read-only readiness |
| `drive-pipeline-run.sh` | Launch-ready happy-path drive + assertions → evidence |
| `snapshot-db.sh` | Dump verify-DB counts into `$EVDIR/db-snapshot.txt` |
| `cleanup.sh` | Truncate `fence_verify`; keep evidence |
