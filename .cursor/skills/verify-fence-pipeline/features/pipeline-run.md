# Pipeline run (Ops happy path)

Ops drops receivables + collections CSVs and runs one CLI command to produce
facility reports.

## Sub-features

- Install package and reach `fence-pipeline run`
- Point at receivables/collections paths and an output directory
- Receive `run_id=` and three report files for that run

## How to get to it (user POV)

From the repo root after Compose Postgres is up and the package is installed:

```bash
fence-pipeline run \
  --receivables <path> \
  --collections <path> \
  --output-dir <dir>
```

Verification uses `tests/fixtures/*.csv` and `--database-url` for `fence_verify`.

## Driving it with the CLI harness

```bash
.cursor/skills/verify-fence-pipeline/helpers/drive-pipeline-run.sh
```

Or manually after `source helpers/verify-env.sh` and `ensure-verify-db.sh` /
`doctor.sh`.

## Gotchas

- Default DSN is the shared `fence` DB — always pass verify URL.
- Full `data/*.csv` runs take ~1–3 minutes; fixtures take seconds.
- Report filenames embed `run_id`; parse it from `cli.log`.
